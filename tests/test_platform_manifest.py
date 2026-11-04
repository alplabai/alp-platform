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
    assert base()["platform"] == "27.2.0"


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
}


def run(manifest=None, files=None, releases=None):
    files = FILES if files is None else files
    m = manifest or base()
    allrel = {(pm.REPOS[k], f"v{v['version']}") for k, v in m["components"].items()}
    rel = allrel if releases is None else releases
    res = pm.check_online(m, lambda r, ref, p: files.get((r, ref, p)), lambda r, t: (r, t) in rel)
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
    monkeypatch.setattr(pm, "github_release_exists", lambda r, t: True)
    assert pm.main(["check", str(EXAMPLE), "--online"]) == 0
    monkeypatch.setattr(pm, "github_release_exists", lambda r, t: False)
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
