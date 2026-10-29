# Packages and evidence — what is committed, and why

The review **records** (findings, dispositions, `review.json`, `REVIEW-23.md`) are committed. The **packages**
(`pkg-*.md`, `package.md`) and the generated `prior-findings.md` are NOT committed, deliberately:

- a package embeds the verbatim diff, and this slice's diff includes the genericization tripwire's own token list
  (the private product names the tripwire exists to catch). Committing a package would republish that list in the
  public repo — the tripwire is self-excluded, but a package quoting it is not.
- packages are a pure function of the pinned SHAs plus the committed inputs. Rebuild them with the command below and
  compare `package_sha256` with the record; the hash is the integrity anchor.

```bash
python3 tools/review/package.py build --repo . --base 152b205 --head 2bd431c \
  --out kit-v0.1/reviews/S3/round-1/pkg-pipeline.md \
  --contract kit-v0.1/reviews/S3/contract.md:contract \
  --gate kit-v0.1/reviews/S3/round-1/gate-evidence.md:deterministic-gates \
  --include onboarding/onboard.py --include onboarding/forge_probe.py \
  --include onboarding/test_out.py --include onboarding/tests/test_onboard.py \
  --include onboarding/tests/test_forge_probe.py --include onboarding/tests/test_test_out.py \
  --include onboarding/README.md
```

The findings documents are committed with one redaction: a quoted evidence line contained a banned token from the
tripwire's list; the token is replaced with `[redacted]` in the committed copy. The package hash therefore pins the
pre-redaction package, and re-review of that package is not byte-reproducible from the committed artifacts alone.
That trade is deliberate: the public-repo boundary outranks byte-reproducibility of an evidence bundle.
