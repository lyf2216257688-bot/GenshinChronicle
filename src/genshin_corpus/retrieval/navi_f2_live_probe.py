"""Retired urllib probe entrypoint; previous attempts remain immutable.

F2 model communication now lives in navi_f2_sdk_transport. This entrypoint
intentionally cannot issue another capability probe or repeat old requests.
"""


def main():
    raise SystemExit("urllib probe retired; two prior attempts retained. No request sent. Use the F2 SDK adapter only after safe recovery is established.")


if __name__ == '__main__':
    main()
