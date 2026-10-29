# Quickstart — install the kit into a target repository (excerpt)

Clone the kit and propose onboarding from inside the repository you
want onboarded:

```sh
git clone https://github.com/example/kit.git
cd YOUR-REPO
python3 ../kit/onboarding/onboard.py propose .
```

The command runs in the default mode, dedicated: the onboarder binds
the target repository without a separate control home being named at
this step, and no `--control-home` argument is shown.

## Upgrading

Re-run the same propose command; the onboarder preserves prior state.