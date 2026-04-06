import argparse
import json

from launcher import LauncherTUI


def read_config(config_path):
    with open(config_path, 'r') as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description='qBc Launcher')
    parser.add_argument('config', help='Path to JSON config file')
    args = parser.parse_args()

    config = read_config(args.config)
    app = LauncherTUI(entries=config['scripts'])
    app.run()


if __name__ == '__main__':
    main()
