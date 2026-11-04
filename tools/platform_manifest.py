# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Alp Lab AB
"""Alp Platform manifest (alp-platform-v1): version grammar, loader, gate checks.

CLI: python tools/platform_manifest.py check <platform.yaml> [--online] [--json]
Exit: 0 all pass, 1 any fail (including an invalid manifest), 2 usage error (--help
included), unreadable file, or GitHub network/API error.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import reprlib
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

import jsonschema
import yaml

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "alp-platform-v1.schema.json"
VERSION_RE = re.compile(r"([0-9]{2})\.([1-9]|1[0-2])\.(0|[1-9][0-9]*)(-rc([1-9][0-9]*))?")
PADDED_RE = re.compile(r"([0-9]{2})\.0([0-9])\.([0-9]+(?:-rc[0-9]+)?)")
SEMVER_RE = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?")

REPOS = {
    "alp-sdk": "alplabai/alp-sdk",
    "tan-cli": "alplabai/tan-cli",
    "alp-sdk-vscode": "alplabai/alp-sdk-vscode",
    "gd32-bridge-firmware": "alplabai/gd32-bridge-firmware",
    "cc3501e-bridge-firmware": "alplabai/cc3501e-bridge-firmware",
}
SOM_FAMILY_DIR = {"AEN": "aen", "V2N": "v2n", "V2M": "v2n-m1"}
NOT_BUILDABLE = {"reserved", "tbd", None}


class ManifestUnreadable(Exception):
    """File missing or not parseable YAML (CLI exit 2)."""


class ManifestInvalid(Exception):
    """Manifest violates schema or semantic rules (CLI exit 1)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Result:
    name: str
    status: str  # pass | fail | skip
    message: str = ""


def _r(name: str, ok: bool, msg_fail: str, msg_ok: str = "ok") -> Result:
    return Result(name, "pass" if ok else "fail", msg_ok if ok else msg_fail)


# --- version grammar -------------------------------------------------------

def parse_version(text: str) -> tuple[int, int, int, Optional[int]]:
    """Platform version -> (yy, m, micro, rc). rc is None for GA."""
    if not isinstance(text, str):
        raise ValueError(f"platform version must be a string, got {_show(text)}")
    m = PADDED_RE.fullmatch(text)
    if m:
        raise ValueError(
            f"{_show(text)}: month must not be zero-padded; write "
            f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
    m = VERSION_RE.fullmatch(text)
    if not m:
        raise ValueError(f"{_show(text)} does not match YY.M.MICRO[-rcN] (e.g. 27.2.0, 27.2.0-rc1)")
    return int(m[1]), int(m[2]), int(m[3]), int(m[5]) if m[5] else None


def _sort_key(v: str):
    yy, mo, mi, rc = parse_version(v)
    # rcN sorts before GA of the same triple; rcN by N.
    return (yy, mo, mi, (1, 0) if rc is None else (0, rc))


def compare_versions(a: str, b: str) -> int:
    ka, kb = _sort_key(a), _sort_key(b)
    return (ka > kb) - (ka < kb)


def _semver(text: str):
    """Component SemVer -> sortable key; a pre-release sorts before its GA."""
    m = SEMVER_RE.fullmatch(text)
    if not m:
        raise ValueError(f"not a SemVer version: {_show(text)}")
    core = tuple(int(x) for x in m.groups()[:3])
    return core, (1, "") if m[4] is None else (0, m[4])


def _core(text) -> Optional[tuple[int, int, int]]:
    """X.Y.Z core only; None when malformed (an absent/malformed bound is unbounded)."""
    m = SEMVER_RE.fullmatch(str(text)) if text is not None else None
    return tuple(int(x) for x in m.groups()[:3]) if m else None


def _gpe(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


# --- loading + offline checks ---------------------------------------------

def check_offline(manifest: dict) -> list[Result]:
    out = []
    try:
        parse_version(manifest.get("platform"))
        out.append(Result("platform-version", "pass", "ok"))
    except ValueError as e:
        out.append(Result("platform-version", "fail", str(e)))
    try:
        datetime.date.fromisoformat(str(manifest.get("released")))
        out.append(Result("released-date", "pass", "ok"))
    except ValueError:
        out.append(Result("released-date", "fail", f"released {_show(manifest.get('released'))} is not a real ISO date"))
    cc = manifest.get("components", {}).get("cc3501e-bridge-firmware", {})
    try:
        stamp, floor = cc["gpe_stamp"], cc["gpe_floor"]
        ok = _gpe(stamp) > _gpe(floor)
        out.append(_r("cc3501e-gpe-floor", ok,
                      f"gpe_stamp {stamp} is at or below gpe_floor {floor}: flashing a GPE stamp "
                      "at or below what the part has already seen PERMANENTLY BRICKS the CC3501E bridge"))
    except (KeyError, ValueError, AttributeError):
        out.append(Result("cc3501e-gpe-floor", "fail", "gpe_stamp / gpe_floor missing or malformed"))
    return out


#: A real manifest is about 1 KiB; anything far larger is not one.
MAX_MANIFEST_BYTES = 64 * 1024

_REPR = reprlib.Repr()
_REPR.maxstring = 80
_REPR.maxother = 80


def _show(value) -> str:
    """Bounded repr for messages about untrusted manifest values."""
    return _REPR.repr(value)


class _NoAliasLoader(yaml.SafeLoader):
    """A manifest has no use for anchors/aliases; refusing them removes
    alias-amplification ("billion laughs") inputs outright."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are not allowed in a platform manifest")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.YAMLError(f"duplicate key {_show(key)} (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return super().construct_mapping(node, deep)


# Dates stay strings: an impossible date (2027-02-30) must reach the validator as
# an invalid manifest (exit 1), not blow up the YAML parser as an unreadable file.
_NoAliasLoader.add_constructor("tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node))


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(k)
            yield from _walk_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_strings(v)


def load_manifest(path) -> dict:
    try:
        raw = Path(path).read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ManifestUnreadable(f"{path}: larger than {MAX_MANIFEST_BYTES} bytes")
        data = yaml.load(raw.decode("utf-8"), Loader=_NoAliasLoader)
    except (OSError, ValueError, RecursionError, yaml.YAMLError) as e:  # RecursionError = deep nesting; ValueError covers UnicodeDecodeError and over-long ints
        raise ManifestUnreadable(f"{path}: {e}") from e
    if not isinstance(data, dict):
        raise ManifestInvalid(["manifest must be a YAML mapping"])
    problems = []
    if any(("\n" in t or "\r" in t) for t in _walk_strings(data)):
        raise ManifestInvalid(["string values and keys must not contain a newline"])
    if data.get("schema") != "alp-platform-v1":
        raise ManifestInvalid([f"unsupported schema {_show(data.get('schema'))}; supported: alp-platform-v1"])
    try:
        parse_version(data.get("platform"))  # gives the named-unpadded-form message
    except ValueError as e:
        problems.append(f"platform: {e}")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for err in sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda e: list(map(str, e.path))):
        if list(err.path) == ["platform"] and problems:
            continue
        problems.append(f"{'/'.join(map(str, err.path)) or '<root>'}: {err.message}")
    if not problems:
        problems += [f"{r.name}: {r.message}" for r in check_offline(data) if r.status == "fail"]
    if problems:
        raise ManifestInvalid(problems)
    return data


# --- online checks ---------------------------------------------------------

Fetch = Callable[[str, str, str], Optional[str]]  # (repo, ref, path) -> text | None
GetRelease = Callable[[str, str], Optional[dict]]  # (repo, tag) -> {"prerelease": bool} | None
GPE_FLOOR_LINE = re.compile(r"^gpe-floor:[ \t]*([0-9]+(?:\.[0-9]+){3})[ \t]*$", re.M)
GUARDED = (ValueError, yaml.YAMLError, AttributeError, KeyError, TypeError, IndexError, RecursionError)


class _Fail(Exception):
    """A check's expected failure; the message is the result text."""


def _fetch_text(repo: str, ref: str, path: str, fetch: Fetch) -> str:
    text = fetch(repo, ref, path)
    if text is None:
        raise _Fail(f"{repo}@{ref}:{path} not found")
    return text


def gpe_stamp_of(head: bytes) -> str:
    """The CC35 vendor image's GPE stamp. Layout: 0x24 major, 0x25 minor,
    0x26 patch, 0x27 PADDING, 0x28 fourth field. A flat 0x24..0x28 slice reads
    the padding as the fourth field and makes different stamps compare equal."""
    if len(head) < 0x29:
        raise ValueError("image header shorter than 0x29 bytes")
    return "%d.%d.%d.%d" % (head[0x24], head[0x25], head[0x26], head[0x28])


def _bound(value) -> Optional[tuple[int, int, int]]:
    """A hw-revisions SDK bound: absent/null = unbounded; anything else must be X.Y.Z[-pre]."""
    if value is None:
        return None
    m = SEMVER_RE.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise ValueError(f"malformed SDK bound {_show(value)}")
    return tuple(int(x) for x in m.groups()[:3])


def check_online(manifest: dict, fetch: Fetch, get_release: GetRelease,
                 fetch_asset_head=None, fetch_published=None) -> list[Result]:
    """Run every online check; none raises on bad remote content (that is a fail).

    fetch(repo, ref, path) -> text|None; get_release(repo, tag) -> {"prerelease": bool}|None;
    fetch_asset_head(repo, tag, name) -> bytes|None;
    fetch_published() -> parsed platform.yaml dicts of every published platform release.
    """
    comps = manifest["components"]
    ver = {k: comps[k]["version"] for k in REPOS}
    tag = {k: f"v{v}" for k, v in ver.items()}
    ga = parse_version(manifest["platform"])[3] is None
    out: list[Result] = []

    def guard(name, fn):
        try:
            out.append(fn())
        except _Fail as e:
            out.append(Result(name, "fail", str(e)))
        except GUARDED as e:
            out.append(Result(name, "fail", f"check errored: {type(e).__name__}: {_show(str(e))}"))

    def cc_image():
        # Hardware-damage check: a stamp at or below what a part has seen permanently
        # bricks it, so the stamp is read from the shipped image and the floor is the
        # MAX of three independent sources, not the author's say-so.
        cc, repo = comps["cc3501e-bridge-firmware"], REPOS["cc3501e-bridge-firmware"]
        asset = f"cc3501e-v{cc['version']}.bin"
        head = fetch_asset_head(repo, tag["cc3501e-bridge-firmware"], asset) if fetch_asset_head else None
        if not head:
            raise _Fail(f"could not read release asset {asset} to verify its GPE stamp")
        real = gpe_stamp_of(head)
        if real != cc["gpe_stamp"]:
            raise _Fail(f"{asset} is stamped {real}, manifest says {_show(cc['gpe_stamp'])}")
        # (value, source, strict): the manifest floor must be strictly cleared; a floor
        # recorded as "the newest shipped stamp" (BUILT_FROM, published platforms) is the
        # stamp of an image that shipped, so re-shipping that same image clears it (>=).
        floors = [(cc["gpe_floor"], "the manifest's gpe_floor", True)]
        text = _fetch_text(repo, tag["cc3501e-bridge-firmware"], "prebuilt/BUILT_FROM", fetch)
        m = GPE_FLOOR_LINE.search(text)
        if not m:
            raise _Fail("no gpe-floor line in prebuilt/BUILT_FROM")
        floors.append((m[1], f"gpe-floor in {repo} prebuilt/BUILT_FROM at {tag['cc3501e-bridge-firmware']}", False))
        if fetch_published is None:
            raise _Fail("cannot read the published platform releases to check their stamps")
        for pub in fetch_published():
            if pub.get("platform") == manifest["platform"]:
                continue
            stamp = pub["components"]["cc3501e-bridge-firmware"]["gpe_stamp"]
            floors.append((stamp, f"gpe_stamp {stamp} already published in platform-v{pub['platform']}", False))
        value, source, strict = max(floors, key=lambda f: (_gpe(f[0]), f[2]))
        ok = _gpe(real) > _gpe(value) if strict else _gpe(real) >= _gpe(value)
        if not ok:
            raise _Fail(f"{asset} is stamped {real}, {'at or below' if strict else 'below'} the effective "
                             f"floor {value} set by {source}: flashing it PERMANENTLY BRICKS any CC3501E "
                             "bridge that has seen that stamp")
        return Result("cc3501e-image-stamp", "pass", f"{asset} stamped {real}; effective floor {value} ({source})")

    def releases():
        problems = []
        for k in REPOS:
            rel = get_release(REPOS[k], tag[k])
            if rel is None:
                problems.append(f"no published release for {REPOS[k]} {tag[k]}")
            elif ga and rel.get("prerelease"):
                problems.append(f"{REPOS[k]} {tag[k]} is marked prerelease, not allowed in a GA platform")
            elif ga and SEMVER_RE.fullmatch(ver[k]).group(4):
                problems.append(f"{k} {ver[k]} is a pre-release version, not allowed in a GA platform")
        return _r("releases-published", not problems, "; ".join(problems))

    def vscode_min():
        text = _fetch_text(REPOS["alp-sdk-vscode"], tag["alp-sdk-vscode"],
                           "packages/alp-core/src/sdk/service.ts", fetch)
        m = re.search(r'MIN_SDK_VERSION\s*=\s*"([^"]+)"', text)
        if not m:
            raise _Fail("MIN_SDK_VERSION not found in service.ts")
        return _r("vscode-min-sdk", _semver(m[1]) <= _semver(ver["alp-sdk"]),
                  f"MIN_SDK_VERSION {m[1]} is above pinned alp-sdk {ver['alp-sdk']}")

    def tan_schema():
        tan_txt = _fetch_text(REPOS["tan-cli"], tag["tan-cli"], "python/tan/core/build_plan.py", fetch)
        sdk_txt = _fetch_text(REPOS["alp-sdk"], tag["alp-sdk"], "metadata/schemas/build-plan-v1.schema.json", fetch)
        m = re.search(r"^SUPPORTED_SCHEMA_VERSION\s*=\s*([0-9]+)", tan_txt, re.M)
        if not m:
            raise _Fail("SUPPORTED_SCHEMA_VERSION not found")
        sdk_v = json.loads(sdk_txt)["properties"]["schemaVersion"]["const"]
        return _r("tan-build-plan-schema", int(m[1]) == sdk_v,
                  f"tan-cli supports build-plan schemaVersion {m[1]}, SDK emits {_show(sdk_v)}")

    def hw(entry_hw):
        name = f"validated-hw:{entry_hw['som']}:{entry_hw['rev']}"
        fam = SOM_FAMILY_DIR[re.match(r"^E1M-(AEN|V2N|V2M)", entry_hw["som"])[1]]
        text = _fetch_text(REPOS["alp-sdk"], tag["alp-sdk"], f"metadata/e1m_modules/{fam}/hw-revisions.yaml", fetch)
        doc = yaml.safe_load(text) or {}
        revs = doc.get("hw_revisions") or {}
        # `rev` is the composed EEPROM / board string `<board_datecode>-<rev key>`
        # (e.g. `2626-r2`), the form a module reports and its PCB carries.
        datecode, _, key = entry_hw["rev"].partition("-")
        want = str(doc.get("board_datecode", ""))
        if datecode != want:
            return Result(name, "fail", f"rev {_show(entry_hw['rev'])}: board datecode {_show(datecode)} does not "
                                        f"match {fam} board_datecode {_show(want)}")
        entry = revs.get(key)
        if not isinstance(entry, dict):
            known = ", ".join(f"{want}-{k}" for k in revs) or "none"
            return Result(name, "fail", f"rev {_show(entry_hw['rev'])} not in {fam}/hw-revisions.yaml (known: {known})")
        if entry.get("status") in NOT_BUILDABLE:
            return Result(name, "fail", f"rev status is {_show(entry.get('status'))}, not buildable")
        lo, hi = _bound(entry.get("min_sdk_version")), _bound(entry.get("max_sdk_version"))
        sdk_core = _core(ver["alp-sdk"])
        return _r(name, (lo is None or lo <= sdk_core) and (hi is None or sdk_core <= hi),
                  f"alp-sdk {ver['alp-sdk']} outside [{entry.get('min_sdk_version')}, {entry.get('max_sdk_version')}]")

    rec = manifest["recorded"]

    def west():
        text = _fetch_text(REPOS["alp-sdk"], tag["alp-sdk"], "west.yml", fetch)
        projects = {p.get("name"): str(p.get("revision")) for p in
                    (yaml.safe_load(text) or {}).get("manifest", {}).get("projects", [])}
        bad = [f"{k}: recorded {_show(rec[k])}, west.yml {_show(projects.get(k))}"
               for k in ("zephyr", "hal_alif") if projects.get(k) != rec[k]]
        return _r("recorded-west", not bad, "; ".join(bad))

    def yocto():
        text = _fetch_text(REPOS["alp-sdk"], tag["alp-sdk"], "meta-alp-sdk/conf/layer.conf", fetch)
        m = re.search(r'^LAYERSERIES_COMPAT_alp-sdk\s*=\s*"([^"]*)"', text, re.M)
        have = set(m[1].split()) if m else None
        return _r("recorded-yocto", have == set(rec["yocto"]),
                  f"recorded {sorted(rec['yocto'])}, layer.conf {sorted(have) if have is not None else 'missing'}")

    def gd32():
        # Only the MAJOR has a single authoritative host-side source
        # (GD32G553_HOST_PROTOCOL_MAJOR); the host defines no single MINOR, so the
        # manifest's protocol minor is NOT checked.
        text = _fetch_text(REPOS["alp-sdk"], tag["alp-sdk"], "include/alp/chips/gd32g553.h", fetch)
        m = re.search(r"#define\s+GD32G553_HOST_PROTOCOL_MAJOR\s+([0-9]+)u?", text)
        if not m:
            raise _Fail("GD32G553_HOST_PROTOCOL_MAJOR not found in gd32g553.h")
        pm_major = comps["gd32-bridge-firmware"]["protocol"].split(".")[0]
        return _r("gd32-protocol-major", int(m[1]) == int(pm_major),
                  f"firmware protocol major {pm_major}, SDK host speaks major {m[1]}")

    guard("cc3501e-image-stamp", cc_image)
    guard("releases-published", releases)
    guard("vscode-min-sdk", vscode_min)
    guard("tan-build-plan-schema", tan_schema)
    for h in manifest["validated_hw"]:
        guard(f"validated-hw:{h['som']}:{h['rev']}", lambda h=h: hw(h))
    guard("recorded-west", west)
    guard("recorded-yocto", yocto)
    guard("gd32-protocol-major", gd32)
    return out


# --- GitHub REST -----------------------------------------------------------

def _api(url: str, accept: str) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"})
    if os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def github_fetch(repo: str, ref: str, path: str) -> Optional[str]:
    body = _api(f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}",
                "application/vnd.github.raw+json")
    return None if body is None else body.decode("utf-8")


def github_asset_head(repo: str, tag: str, name: str, nbytes: int = 64) -> Optional[bytes]:
    """First `nbytes` of a release asset, or None when the release/asset is missing."""
    body = _api(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", "application/vnd.github+json")
    if body is None:
        return None
    for a in json.loads(body).get("assets", []):
        if a.get("name") == name:
            req = urllib.request.Request(a["url"], headers={"Accept": "application/octet-stream",
                                                            "Range": f"bytes=0-{nbytes - 1}"})
            if os.environ.get("GITHUB_TOKEN"):
                req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read(nbytes)
    return None


def github_get_release(repo: str, tag: str) -> Optional[dict]:
    body = _api(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", "application/vnd.github+json")
    return None if body is None else json.loads(body)


PLATFORM_REPO = "alplabai/alp-platform"


def _asset_bytes(asset: dict) -> bytes:
    req = urllib.request.Request(asset["url"], headers={"Accept": "application/octet-stream"})
    if os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(MAX_MANIFEST_BYTES + 1)


def github_published_manifests() -> list[dict]:
    """Parsed platform.yaml of every published (non-draft) platform-v* release."""
    out, page = [], 1
    while True:
        body = _api(f"https://api.github.com/repos/{PLATFORM_REPO}/releases?per_page=100&page={page}",
                    "application/vnd.github+json")
        rels = json.loads(body) if body else []
        for rel in rels:
            if rel.get("draft") or not str(rel.get("tag_name", "")).startswith("platform-v"):
                continue
            assets = [a for a in rel.get("assets", []) if a.get("name") == "platform.yaml"]
            if not assets:
                raise ValueError(f"release {rel['tag_name']} has no platform.yaml asset")
            data = _asset_bytes(assets[0])
            if len(data) > MAX_MANIFEST_BYTES:
                raise ValueError(f"release {rel['tag_name']}: platform.yaml too large")
            out.append(yaml.load(data.decode("utf-8"), Loader=_NoAliasLoader))
        if len(rels) < 100:
            return out
        page += 1


# --- CLI -------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="platform_manifest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("manifest")
    c.add_argument("--online", action="store_true")
    c.add_argument("--json", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    try:
        m = load_manifest(args.manifest)
        results = check_offline(m)
    except ManifestUnreadable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ManifestInvalid as e:
        results = [Result("manifest", "fail", p) for p in e.problems]
        m = None
    if m is not None and args.online:
        try:
            results += check_online(m, github_fetch, github_get_release, github_asset_head,
                                   github_published_manifests)
        except (urllib.error.URLError, OSError) as e:
            print(f"error: GitHub API unreachable: {e}", file=sys.stderr)
            return 2
    failed = any(r.status == "fail" for r in results)
    if args.json:
        print(json.dumps({"ok": not failed, "results": [asdict(r) for r in results]}, indent=2))
    else:
        for r in results:
            print(f"[{r.status.upper():4}] {r.name}: {r.message}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
