# Releasing Patchcord

Patchcord keeps its PEP 440 version in `pyproject.toml` and uses matching
annotated Git tags with a `v` prefix. For example, package version `0.3.0` is
released from tag `v0.3.0`.

The release workflow accepts only a tag whose version exactly matches the
committed project version. It runs the complete test matrix, builds the wheel
and source distribution once, smoke-tests both artifacts in isolation, and
publishes those exact files to PyPI and a GitHub Release.

## One-time repository configuration

1. Create a protected GitHub environment named `pypi`.
2. Restrict the environment to tags matching `v*` and require a maintainer's
   approval before deployment.
3. Configure a PyPI Trusted Publisher for:
   - owner `totocaster`;
   - repository `patchcord`;
   - workflow `release.yml`; and
   - environment `pypi`.
4. Protect `main` and require the `CI / test` matrix before merging.

The release workflow uses OpenID Connect. Do not add a long-lived PyPI token to
the repository.

## Prepare a release

Update uv before running release commands, then create a release branch:

```console
uv self update
VERSION=0.3.0
git switch -c "release/$VERSION"
uv version "$VERSION"
```

Use `0.3.0rc1`-style PEP 440 versions for prereleases. Commit both
`pyproject.toml` and `uv.lock`, open a pull request, and merge it only after CI
passes.

## Publish

Update local `main`, confirm the version, and tag the exact merge commit:

```console
VERSION=0.3.0
git switch main
git pull --ff-only
uv version --short
git tag -a "v$VERSION" -m "Patchcord $VERSION"
git push origin "v$VERSION"
```

Approve the `pypi` environment deployment. The workflow publishes to PyPI
before creating the GitHub Release. PyPI files and release tags are immutable:
never move or reuse a released version tag.

Verify the public release in a clean tool environment:

```console
VERSION=0.3.0
uv tool run --from "patchcord==$VERSION" patchcord --version
```
