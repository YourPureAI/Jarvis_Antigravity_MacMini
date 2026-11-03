"""Entry point for the crypto-tracker skill."""
import argparse
import json
import os


def main() -> None:
    p = argparse.ArgumentParser(description="crypto-tracker skill")
    p.add_argument("--echo", default="ok")
    a = p.parse_args()
    print(json.dumps({"ok": True, "echo": a.echo, "data_dir": os.environ.get("AGENT_SKILL_DATA")}))


if __name__ == "__main__":
    main()
