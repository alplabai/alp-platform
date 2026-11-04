# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Alp Lab AB
import copy
import json
from pathlib import Path

import pytest
import yaml

import platform_manifest as pm

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "platform.example.yaml"


def base() -> dict:
    return pm.load_manifest(EXAMPLE)


def write(tmp_path, data) -> Path:
    p = tmp_path / "platform.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


# --- version grammar --------------------------------------------------------

def test_padded_month_refused_names_unpadded():
    with pytest.raises(ValueError, match=r"27\.2\.0"):
        pm.parse_version("27.02.0")


@pytest.mark.parametrize("bad", ["27.13.0", "27.0.0", "7.2.0", "27.2", "27.2.00", "27.2.0-rc0", "v27.2.0"])
def test_bad_versions(bad):
    with pytest.raises(ValueError):
        pm.parse_version(bad)


def test_rc_ordering():
    order = ["27.2.0-rc1", "27.2.0-rc2", "27.2.0-rc10", "27.2.0", "27.2.1", "27.10.0", "28.1.0"]
    for a, b in zip(order, order[1:]):
        assert pm.compare_versions(a, b) == -1
        assert pm.compare_versions(b, a) == 1
    assert pm.compare_versions("27.2.0", "27.2.0") == 0


# --- schema / load ----------------------------------------------------------

def test_example_validates():
    assert base()["platform"] == "26.11.0"


def test_unknown_top_level_key_refused(tmp_path):
    d = base(); d["extra"] = 1
    with pytest.raises(pm.ManifestInvalid):
        pm.load_manifest(write(tmp_path, d))


def test_unknown_recorded_key_refused(tmp_path):
    d = base(); d["recorded"]["foo"] = "x"
    with pytest.raises(pm.ManifestInvalid):
        pm.load_manifest(write(tmp_path, d))


def test_extra_component_key_allowed(tmp_path):
    d = base(); d["components"]["tan-cli"]["note"] = "x"
    pm.load_manifest(write(tmp_path, d))


def test_extra_component_refused(tmp_path):
    d = base(); d["components"]["signex"] = {"version": "1.0.0"}
    with pytest.raises(pm.ManifestInvalid):
        pm.load_manifest(write(tmp_path, d))


def test_wrong_schema_names_supported(tmp_path):
    d = base(); d["schema"] = "alp-platform-v2"
    with pytest.raises(pm.ManifestInvalid, match="alp-platform-v1"):
        pm.load_manifest(write(tmp_path, d))


def test_padded_platform_in_manifest(tmp_path):
    d = base(); d["platform"] = "27.02.0"
    with pytest.raises(pm.ManifestInvalid, match=r"27\.2\.0"):
        pm.load_manifest(write(tmp_path, d))


def test_fake_date_refused(tmp_path):
    d = base(); d["released"] = "2027-02-30"
    with pytest.raises(pm.ManifestInvalid, match="date"):
        pm.load_manifest(write(tmp_path, d))


@pytest.mark.parametrize("stamp,ok", [
    ("0.254.43.0", True), ("0.254.42.1", True), ("0.255.0.0", True), ("1.0.0.0", True),
    ("0.254.42.0", False), ("0.254.41.9", False), ("0.254.9.0", False),
])
def test_gpe_numeric(tmp_path, stamp, ok):
    d = base(); d["components"]["cc3501e-bridge-firmware"]["gpe_stamp"] = stamp
    if ok:
        pm.load_manifest(write(tmp_path, d))
    else:
        with pytest.raises(pm.ManifestInvalid, match="BRICKS"):
            pm.load_manifest(write(tmp_path, d))


def test_gpe_is_numeric_not_lexical(tmp_path):
    d = base()
    d["components"]["cc3501e-bridge-firmware"].update(gpe_stamp="0.254.100.0", gpe_floor="0.254.42.0")
    pm.load_manifest(write(tmp_path, d))


# --- online checks with a fake GitHub ---------------------------------------

SDK_TAG = "v0.18.0"
FILES = {
    ("alplabai/alp-sdk-vscode", "v0.8.0", "packages/alp-core/src/sdk/service.ts"):
        'export const MIN_SDK_VERSION = "0.10.0";\n',
    ("alplabai/tan-cli", "v0.8.0", "python/tan/core/build_plan.py"): "SUPPORTED_SCHEMA_VERSION = 1\n",
    ("alplabai/alp-sdk", SDK_TAG, "metadata/schemas/build-plan-v1.schema.json"):
        json.dumps({"properties": {"schemaVersion": {"const": 1}}}),
    ("alplabai/alp-sdk", SDK_TAG, "metadata/e1m_modules/aen/hw-revisions.yaml"): yaml.safe_dump({
        "board_datecode": "2626",
        "hw_revisions": {
            "r1": {"status": "production", "min_sdk_version": "0.3.0", "max_sdk_version": None},
            "r2": {"status": "production", "min_sdk_version": None, "max_sdk_version": None},
            "r3": {"status": "reserved"},
            "r4": {"status": "preview", "min_sdk_version": "0.19.0"},
            "r5": {"status": "deprecated", "max_sdk_version": "0.17.9"},
            "r6": {},
        }}),
    ("alplabai/alp-sdk", SDK_TAG, "west.yml"): yaml.safe_dump({"manifest": {"projects": [
        {"name": "zephyr", "revision": "v4.4.1"}, {"name": "hal_alif", "revision": "v2.3.0"}]}}),
    ("alplabai/alp-sdk", SDK_TAG, "meta-alp-sdk/conf/layer.conf"):
        'LAYERSERIES_COMPAT_alp-sdk = "kirkstone scarthgap"\n',
    ("alplabai/alp-sdk", SDK_TAG, "include/alp/chips/gd32g553.h"):
        "#define GD32G553_HOST_PROTOCOL_MAJOR 0u\n",
    ("alplabai/cc3501e-bridge-firmware", "v0.9.2", "prebuilt/BUILT_FROM"): "# x\ngpe-floor:    0.254.38.0\n",
}


def image_head(stamp="0.254.43.0"):
    a, b, c, d = (int(x) for x in stamp.split("."))
    h = bytearray(64)
    h[0x24], h[0x25], h[0x26], h[0x27], h[0x28] = a, b, c, 0xEE, d  # 0x27 is padding
    return bytes(h)


def run(manifest=None, files=None, releases=None, head=None, published=(), prerelease=()):
    files = dict(FILES if files is None else files)
    head = image_head() if head is None else head
    m = manifest or base()
    allrel = {(pm.REPOS[k], f"v{v['version']}") for k, v in m["components"].items()}
    rel = allrel if releases is None else releases
    res = pm.check_online(m, lambda r, ref, p: files.get((r, ref, p)),
                          lambda r, t: {"prerelease": (r, t) in prerelease} if (r, t) in rel else None,
                          lambda r, t, n: head or None, lambda: list(published))
    return {r.name: r for r in res}


def test_all_pass():
    res = run()
    assert all(r.status == "pass" for r in res.values()), res


def test_a_release_missing():
    m = base()
    rel = {(pm.REPOS[k], f"v{v['version']}") for k, v in m["components"].items()}
    rel.discard(("alplabai/tan-cli", "v0.8.0"))
    r = run(releases=rel)["releases-published"]
    assert r.status == "fail" and "tan-cli" in r.message


def test_b_min_sdk_above_pinned():
    f = dict(FILES)
    f[("alplabai/alp-sdk-vscode", "v0.8.0", "packages/alp-core/src/sdk/service.ts")] = \
        'export const MIN_SDK_VERSION = "0.18.1";'
    assert run(files=f)["vscode-min-sdk"].status == "fail"


def test_b_min_sdk_equal_ok_and_missing_file():
    f = dict(FILES)
    f[("alplabai/alp-sdk-vscode", "v0.8.0", "packages/alp-core/src/sdk/service.ts")] = \
        'export const MIN_SDK_VERSION = "0.18.0";'
    assert run(files=f)["vscode-min-sdk"].status == "pass"
    del f[("alplabai/alp-sdk-vscode", "v0.8.0", "packages/alp-core/src/sdk/service.ts")]
    assert run(files=f)["vscode-min-sdk"].status == "fail"


def test_c_schema_mismatch():
    f = dict(FILES)
    f[("alplabai/tan-cli", "v0.8.0", "python/tan/core/build_plan.py")] = "SUPPORTED_SCHEMA_VERSION = 2\n"
    assert run(files=f)["tan-build-plan-schema"].status == "fail"


@pytest.mark.parametrize("rev,status", [
    ("2626-r1", "pass"), ("2626-r2", "pass"),
    ("2626-r3", "fail"),   # reserved
    ("2626-r4", "fail"),   # min above pinned
    ("2626-r5", "fail"),   # max below pinned
    ("2626-r6", "fail"),   # no status
    ("2626-r9", "fail"),   # unknown
    ("2625-r2", "fail"),   # wrong board datecode for the family
])
def test_d_validated_hw(rev, status):
    m = base(); m["validated_hw"] = [{"som": "E1M-AEN801", "rev": rev}]
    assert run(manifest=m)[f"validated-hw:E1M-AEN801:{rev}"].status == status


def test_e_recorded_mismatch():
    m = base(); m["recorded"]["zephyr"] = "v4.4.0"
    assert run(manifest=m)["recorded-west"].status == "fail"
    m = base(); m["recorded"]["yocto"] = ["kirkstone"]
    assert run(manifest=m)["recorded-yocto"].status == "fail"
    m = base(); m["recorded"]["yocto"] = ["scarthgap", "kirkstone"]  # order-insensitive
    assert run(manifest=m)["recorded-yocto"].status == "pass"


def test_f_gd32_major():
    m = base(); m["components"]["gd32-bridge-firmware"]["protocol"] = "1.0"
    assert run(manifest=m)["gd32-protocol-major"].status == "fail"


# --- CLI --------------------------------------------------------------------

def test_cli_exit_codes(tmp_path, capsys):
    assert pm.main(["check", str(EXAMPLE)]) == 0
    d = base(); d["components"]["cc3501e-bridge-firmware"]["gpe_stamp"] = "0.254.42.0"
    assert pm.main(["check", str(write(tmp_path, d))]) == 1
    assert pm.main(["check", str(tmp_path / "nope.yaml")]) == 2
    assert pm.main([]) == 2
    capsys.readouterr()
    assert pm.main(["check", str(EXAMPLE), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_online_uses_injected_fetch(monkeypatch, capsys):
    monkeypatch.setattr(pm, "github_fetch", lambda r, ref, p: FILES.get((r, ref, p)))
    monkeypatch.setattr(pm, "github_get_release", lambda r, t: {"prerelease": False})
    monkeypatch.setattr(pm, "github_asset_head", lambda r, t, n: image_head())
    monkeypatch.setattr(pm, "github_published_manifests", lambda: [])
    assert pm.main(["check", str(EXAMPLE), "--online"]) == 0
    monkeypatch.setattr(pm, "github_get_release", lambda r, t: None)
    assert pm.main(["check", str(EXAMPLE), "--online"]) == 1


def test_rev_must_be_composed_form(tmp_path):
    d = copy.deepcopy(base()); d["validated_hw"] = [{"som": "E1M-AEN801", "rev": "r2"}]
    with pytest.raises(pm.ManifestInvalid):
        pm.load_manifest(write(tmp_path, d))


def test_yaml_alias_refused(tmp_path):
    f = tmp_path / "p.yaml"; f.write_text("a: &x [1, 2]\nb: *x\n")
    with pytest.raises(pm.ManifestUnreadable, match="alias"):
        pm.load_manifest(f)


def test_oversized_manifest_refused(tmp_path):
    f = tmp_path / "p.yaml"; f.write_text("x: " + "y" * (pm.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(pm.ManifestUnreadable, match="larger than"):
        pm.load_manifest(f)


def test_version_error_repr_is_bounded():
    with pytest.raises(ValueError) as exc:
        pm.parse_version("9" * 10000)
    assert len(str(exc.value)) < 400


def test_overlong_int_is_unreadable(tmp_path):
    f = tmp_path / "p.yaml"; f.write_text("platform: " + "9" * 5000 + "\n")
    with pytest.raises(pm.ManifestUnreadable):
        pm.load_manifest(f)


def test_image_stamp_matches():
    assert run()["cc3501e-image-stamp"].status == "pass"


def test_image_stamp_mismatch_fails():
    assert run(head=image_head("0.254.38.0"))["cc3501e-image-stamp"].status == "fail"


def test_image_stamp_padding_byte_ignored():
    assert pm.gpe_stamp_of(image_head("0.254.43.7")) == "0.254.43.7"


def test_image_missing_fails():
    assert run(head=b"")["cc3501e-image-stamp"].status == "fail"


# --- effective GPE floor: max of manifest, BUILT_FROM, published platforms ----

def _pub(platform, stamp):
    return {"platform": platform, "components": {"cc3501e-bridge-firmware": {"gpe_stamp": stamp}}}


BF = ("alplabai/cc3501e-bridge-firmware", "v0.9.2", "prebuilt/BUILT_FROM")


def test_floor_set_by_manifest_is_strict(tmp_path):
    m = base(); m["components"]["cc3501e-bridge-firmware"]["gpe_floor"] = "0.254.43.0"
    r = run(manifest=m)["cc3501e-image-stamp"]
    assert r.status == "fail" and "manifest's gpe_floor" in r.message and "BRICKS" in r.message


def test_floor_set_by_built_from():
    f = dict(FILES); f[BF] = "gpe-floor:  0.254.44.0\n"
    r = run(files=f)["cc3501e-image-stamp"]
    assert r.status == "fail" and "BUILT_FROM" in r.message


def test_built_from_equal_to_stamp_passes():
    f = dict(FILES); f[BF] = "gpe-floor:  0.254.43.0\n"
    assert run(files=f)["cc3501e-image-stamp"].status == "pass"


def test_floor_set_by_published_release():
    r = run(published=[_pub("26.10.0", "0.254.50.0")])["cc3501e-image-stamp"]
    assert r.status == "fail" and "platform-v26.10.0" in r.message


def test_published_lower_and_own_tag_ignored():
    r = run(published=[_pub("26.10.0", "0.254.40.0"), _pub("26.11.0", "0.254.99.0")])
    assert r["cc3501e-image-stamp"].status == "pass"


def test_built_from_missing_or_malformed_fails():
    f = dict(FILES); f[BF] = "no floor here\n"
    assert run(files=f)["cc3501e-image-stamp"].status == "fail"
    f = {k: v for k, v in FILES.items()}
    assert run(files=f, head=image_head("0.254.43.0"))["cc3501e-image-stamp"].status == "pass"


def test_published_unreadable_is_fail_not_crash():
    r = pm.check_online(base(), lambda r, ref, p: FILES.get((r, ref, p)),
                        lambda r, t: {"prerelease": False}, lambda r, t, n: image_head(), None)
    assert {x.name: x for x in r}["cc3501e-image-stamp"].status == "fail"


# --- hardening ----------------------------------------------------------------

def test_non_ascii_digits_and_newline_refused(tmp_path):
    d = base(); d["platform"] = "27.2.0\n"
    with pytest.raises(pm.ManifestInvalid):
        pm.load_manifest(write(tmp_path, d))
    d = base(); d["components"]["tan-cli"]["version"] = "0.٨.0"
    with pytest.raises(pm.ManifestInvalid):
        pm.load_manifest(write(tmp_path, d))
    with pytest.raises(ValueError):
        pm.parse_version("27.2.0\n")
    with pytest.raises(ValueError):
        pm.parse_version("2٧.2.0")


def test_newline_in_any_string_refused(tmp_path):
    d = base(); d["bench_evidence"] = "http://x\nevil"
    with pytest.raises(pm.ManifestInvalid, match="newline"):
        pm.load_manifest(write(tmp_path, d))


def test_ga_refuses_prerelease_component_and_prerelease_release():
    m = base(); m["components"]["tan-cli"]["version"] = "0.8.0-rc1"
    assert run(manifest=m)["releases-published"].status == "fail"
    pre = {("alplabai/tan-cli", "v0.8.0")}
    assert run(prerelease=pre)["releases-published"].status == "fail"
    m = base(); m["platform"] = "27.2.0-rc1"; m["components"]["tan-cli"]["version"] = "0.8.0-rc1"
    assert run(manifest=m, prerelease=pre)["releases-published"].status == "pass"


def test_gd32_macro_missing_is_fail():
    f = dict(FILES); f[("alplabai/alp-sdk", SDK_TAG, "include/alp/chips/gd32g553.h")] = "/* nothing */"
    assert run(files=f)["gd32-protocol-major"].status == "fail"


def test_malformed_remote_content_is_fail_not_crash():
    f = dict(FILES)
    f[("alplabai/alp-sdk", SDK_TAG, "west.yml")] = "a: [unclosed"
    f[("alplabai/alp-sdk", SDK_TAG, "metadata/schemas/build-plan-v1.schema.json")] = "not json"
    f[("alplabai/alp-sdk", SDK_TAG, "metadata/e1m_modules/aen/hw-revisions.yaml")] = "- just\n- a list\n"
    res = run(files=f)
    for n in ("recorded-west", "tan-build-plan-schema", "validated-hw:E1M-AEN801:2626-r2"):
        assert res[n].status == "fail"


def test_hw_bounds_null_unbounded_malformed_fails():
    def hwrun(entry):
        f = dict(FILES)
        f[("alplabai/alp-sdk", SDK_TAG, "metadata/e1m_modules/aen/hw-revisions.yaml")] = yaml.safe_dump(
            {"board_datecode": "2626", "hw_revisions": {"r2": entry}})
        return run(files=f)["validated-hw:E1M-AEN801:2626-r2"].status
    assert hwrun({"status": "production", "min_sdk_version": None}) == "pass"
    assert hwrun({"status": "production"}) == "pass"
    assert hwrun({"status": "production", "min_sdk_version": "garbage"}) == "fail"
    assert hwrun({"status": "production", "max_sdk_version": 7}) == "fail"


def test_duplicate_keys_refused(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(EXAMPLE.read_text() + "platform: 27.2.1\n")
    with pytest.raises(pm.ManifestUnreadable, match="duplicate"):
        pm.load_manifest(p)


def test_deep_nesting_is_unreadable(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("x: " + "[" * 5000 + "]" * 5000 + "\n")
    with pytest.raises(pm.ManifestUnreadable):
        pm.load_manifest(p)


def test_impossible_yaml_date_is_invalid_not_unreadable(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(EXAMPLE.read_text().replace("2026-11-30", "2026-11-31"))
    with pytest.raises(pm.ManifestInvalid):
        pm.load_manifest(p)
    assert pm.main(["check", str(p)]) == 1
