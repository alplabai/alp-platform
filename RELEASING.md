# Releasing an Alp Platform line

![Six release steps: freeze, manifest, manifest gate, bench, promote, tag and publish](docs/images/release-flow.svg)

Components always release before the platform tag.

1. **Freeze** about two weeks before the planned release date. Each component cuts its own release
   through its own flow (alp-sdk, tan-cli, alp-sdk-vscode, gd32-bridge-firmware,
   cc3501e-bridge-firmware).
2. **Write the manifest.** Branch `line/YY.M` from `main`, add `platform.yaml` at the root
   (start from `examples/platform.example.yaml`), tag `platform-vYY.M.0-rc1`.
3. **Run the gate.** `python tools/platform_manifest.py check platform.yaml --online`
   (CI runs it on every push and PR to `line/**`). Fix the manifest until it passes.
4. **Bench-validate** every `validated_hw` rev: build with tan from the manifest, run the
   HIL suite **serially** on labgrid-reserved boards (one board at a time, reserve before
   use), and link the evidence in `bench_evidence`.
5. **Promote** components from rc to GA, update the manifest, re-run the gate. Re-bench
   only a component whose binary changed.
6. **Tag `platform-vYY.M.0`.** The `release` workflow checks the tag equals `platform`,
   re-runs the online gate, and publishes the release with `platform.yaml` as an asset.
   Add the component table and prebuilt images to the release page.

## Cadence

![A new line every one to two months: the latest line gets all fixes, the previous line security and brick-risk fixes for three months](docs/images/support-windows.svg)

A new line ships every one to two months, when a bench-validated set is ready; it takes the month it ships in. The first line is 26.11 (November 2026). The latest line gets every MICRO fix; the previous line gets security and brick-risk fixes for three months after the next line ships.

## MICRO vs new line

A MICRO may take component PATCH releases and add a validated `hw_rev`. A component MINOR
or MAJOR, any ABI break, a bridge wire-protocol change, or a Zephyr / hal_alif / Yocto
bump needs a new line.

## CC3501E brick warning

Flashing a CC3501E GPE stamp at or below the floor the part has already seen
**permanently bricks the bridge**. The gate refuses any manifest with
`gpe_stamp <= gpe_floor`; never bypass it, and never raise `gpe_floor` above a stamp you
have not confirmed on the part. Raise the floor only after the new image is flashed and
verified on the bench.

## CI token

The five component repos are public, so the default `GITHUB_TOKEN` can read them. The
workflows prefer an optional `PLATFORM_GATE_TOKEN` secret when it is set, for example to
avoid API rate limits or if a component repo becomes private.
