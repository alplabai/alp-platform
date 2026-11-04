# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Alp Lab AB
"""Alp Platform manifest (alp-platform-v1): version grammar, loader, gate checks.

CLI: python tools/platform_manifest.py check <platform.yaml> [--online] [--json]
Exit: 0 all pass, 1 any fail, 2 usage / unreadable file.
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
VERSION_RE = re.compile(r"^(\d{2})\.([1-9]|1[0-2])\.(0|[1-9]\d*)(-rc([1-9]\d*))?$")
PADDED_RE = re.compile(r"^(\d{2})\.0(\d)\.(\d+(?:-rc\d+)?)$")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$")

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
    m = PADDED_RE.match(text)
    if m:
        raise ValueError(
            f"{_show(text)}: month must not be zero-padded; write "
            f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
    m = VERSION_RE.match(text)
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
    m = SEMVER_RE.match(text)
    if not m:
        raise ValueError(f"not a SemVer version: {_show(text)}")
    core = tuple(int(x) for x in m.groups()[:3])
    return core, (1, "") if m[4] is None else (0, m[4])


def _core(text) -> Optional[tuple[int, int, int]]:
    """X.Y.Z core only; None when malformed (an absent/malformed bound is unbounded)."""
    m = SEMVER_RE.match(str(text)) if text is not None else None
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


def load_manifest(path) -> dict:
    try:
        raw = Path(path).read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ManifestUnreadable(f"{path}: larger than {MAX_MANIFEST_BYTES} bytes")
        data = yaml.load(raw.decode("utf-8"), Loader=_NoAliasLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise ManifestUnreadable(f"{path}: {e}") from e
    if not isinstance(data, dict):
        raise ManifestInvalid(["manifest must be a YAML mapping"])
    if isinstance(data.get("released"), datetime.date):  # YAML parses ISO dates natively
        data["released"] = data["released"].isoformat()
    problems = []
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
ReleaseExists = Callable[[str, str], bool]  # (repo, tag) -> bool


def _fetch_or_fail(name: str, fetch: Fetch, repo: str, ref: str, path: str):
    text = fetch(repo, ref, path)
    if text is None:
        return None, Result(name, "fail", f"{repo}@{ref}:{path} not found")
    return text, None


def check_online(manifest: dict, fetch: Fetch, release_exists: ReleaseExists) -> list[Result]:
    comps = manifest["components"]
    ver = {k: comps[k]["version"] for k in REPOS}
    tag = {k: f"v{v}" for k, v in ver.items()}
    out = []

    # a. published releases
    missing = [f"{REPOS[k]} {tag[k]}" for k in REPOS if not release_exists(REPOS[k], tag[k])]
    out.append(_r("releases-published", not missing, "no published release for: " + ", ".join(missing)))

    # b. vscode MIN_SDK_VERSION <= pinned sdk
    name = "vscode-min-sdk"
    text, err = _fetch_or_fail(name, fetch, REPOS["alp-sdk-vscode"], tag["alp-sdk-vscode"],
                               "packages/alp-core/src/sdk/service.ts")
    if err:
        out.append(err)
    else:
        m = re.search(r'MIN_SDK_VERSION\s*=\s*"([^"]+)"', text)
        if not m:
            out.append(Result(name, "fail", "MIN_SDK_VERSION not found in service.ts"))
        else:
            ok = _semver(m[1]) <= _semver(ver["alp-sdk"])
            out.append(_r(name, ok, f"MIN_SDK_VERSION {m[1]} is above pinned alp-sdk {ver['alp-sdk']}"))

    # c. tan build-plan schema == SDK emitted schemaVersion
    name = "tan-build-plan-schema"
    tan_txt, err = _fetch_or_fail(name, fetch, REPOS["tan-cli"], tag["tan-cli"], "python/tan/core/build_plan.py")
    sdk_txt, err2 = _fetch_or_fail(name, fetch, REPOS["alp-sdk"], tag["alp-sdk"],
                                   "metadata/schemas/build-plan-v1.schema.json")
    if err or err2:
        out.append(err or err2)
    else:
        m = re.search(r"^SUPPORTED_SCHEMA_VERSION\s*=\s*(\d+)", tan_txt, re.M)
        try:
            sdk_v = json.loads(sdk_txt)["properties"]["schemaVersion"]["const"]
        except (ValueError, KeyError, TypeError):
            sdk_v = None
        if not m or sdk_v is None:
            out.append(Result(name, "fail", "could not read SUPPORTED_SCHEMA_VERSION or SDK schemaVersion"))
        else:
            out.append(_r(name, int(m[1]) == sdk_v,
                          f"tan-cli supports build-plan schemaVersion {m[1]}, SDK emits {sdk_v}"))

    # d. validated_hw
    sdk_core = _core(ver["alp-sdk"])
    for hw in manifest["validated_hw"]:
        name = f"validated-hw:{hw['som']}:{hw['rev']}"
        fam = SOM_FAMILY_DIR[re.match(r"^E1M-(AEN|V2N|V2M)", hw["som"])[1]]
        text, err = _fetch_or_fail(name, fetch, REPOS["alp-sdk"], tag["alp-sdk"],
                                   f"metadata/e1m_modules/{fam}/hw-revisions.yaml")
        if err:
            out.append(err)
            continue
        doc = yaml.safe_load(text) or {}
        revs = doc.get("hw_revisions") or {}
        # `rev` is the composed EEPROM / board string `<board_datecode>-<rev key>`
        # (e.g. `2626-r2`), the form a module reports and its PCB carries.
        datecode, _, key = hw["rev"].partition("-")
        want = str(doc.get("board_datecode", ""))
        if datecode != want:
            out.append(Result(name, "fail", f"rev {hw['rev']!r}: board datecode {datecode!r} does not "
                                            f"match {fam} board_datecode {want!r}"))
            continue
        entry = revs.get(key)
        if not isinstance(entry, dict):
            known = ", ".join(f"{want}-{k}" for k in revs) or "none"
            out.append(Result(name, "fail", f"rev {hw['rev']!r} not in {fam}/hw-revisions.yaml "
                                            f"(known: {known})"))
            continue
        if entry.get("status") in NOT_BUILDABLE:
            out.append(Result(name, "fail", f"rev status is {entry.get('status')!r}, not buildable"))
            continue
        lo, hi = _core(entry.get("min_sdk_version")), _core(entry.get("max_sdk_version"))
        ok = (lo is None or lo <= sdk_core) and (hi is None or sdk_core <= hi)
        out.append(_r(name, ok, f"alp-sdk {ver['alp-sdk']} outside [{entry.get('min_sdk_version')}, "
                                f"{entry.get('max_sdk_version')}]"))

    # e. recorded vs west.yml / layer.conf
    rec = manifest["recorded"]
    name = "recorded-west"
    text, err = _fetch_or_fail(name, fetch, REPOS["alp-sdk"], tag["alp-sdk"], "west.yml")
    if err:
        out.append(err)
    else:
        projects = {p.get("name"): str(p.get("revision")) for p in
                    (yaml.safe_load(text) or {}).get("manifest", {}).get("projects", [])}
        bad = [f"{k}: recorded {rec[k]!r}, west.yml {projects.get(k)!r}"
               for k in ("zephyr", "hal_alif") if projects.get(k) != rec[k]]
        out.append(_r(name, not bad, "; ".join(bad)))
    name = "recorded-yocto"
    text, err = _fetch_or_fail(name, fetch, REPOS["alp-sdk"], tag["alp-sdk"], "meta-alp-sdk/conf/layer.conf")
    if err:
        out.append(err)
    else:
        m = re.search(r'^LAYERSERIES_COMPAT_alp-sdk\s*=\s*"([^"]*)"', text, re.M)
        have = set(m[1].split()) if m else None
        out.append(_r(name, have == set(rec["yocto"]),
                      f"recorded {sorted(rec['yocto'])}, layer.conf {sorted(have) if have is not None else 'missing'}"))

    # f. gd32 protocol. Only the MAJOR has a single authoritative host-side source
    # (GD32G553_HOST_PROTOCOL_MAJOR); the host defines no single MINOR.
    name = "gd32-protocol-major"
    text, err = _fetch_or_fail(name, fetch, REPOS["alp-sdk"], tag["alp-sdk"], "include/alp/chips/gd32g553.h")
    if err:
        out.append(err)
    else:
        m = re.search(r"#define\s+GD32G553_HOST_PROTOCOL_MAJOR\s+(\d+)u?", text)
        pm = comps["gd32-bridge-firmware"]["protocol"].split(".")[0]
        if not m:
            out.append(Result(name, "skip", "skipped: source not found (GD32G553_HOST_PROTOCOL_MAJOR)"))
        else:
            out.append(_r(name, m[1] == str(int(pm)),
                          f"firmware protocol major {pm}, SDK host speaks major {m[1]}"))
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


def github_release_exists(repo: str, tag: str) -> bool:
    return _api(f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
                "application/vnd.github+json") is not None


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
            results += check_online(m, github_fetch, github_release_exists)
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
