from pathlib import Path
import sys


def main():
    base_dir = Path(__file__).resolve().parents[1]
    manage_py = base_dir / "manage.py"
    if not manage_py.exists():
        raise SystemExit("Could not find manage.py from scripts/warm_dashboard_cache.py")

    sys.path.insert(0, str(base_dir))
    from manage import main as manage_main

    sys.argv = ["manage.py", "warm_dashboard_cache", *sys.argv[1:]]
    manage_main()


if __name__ == "__main__":
    main()
