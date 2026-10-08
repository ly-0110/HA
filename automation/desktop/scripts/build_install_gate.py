from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "out/IoTExperimentWorkbench-Setup.exe")
    args = parser.parse_args()
    compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/r:System.Windows.Forms.dll",
               "/r:System.Web.Extensions.dll", "/win32icon:" + str(ROOT / "assets/workbench.ico"),
               "/out:" + str(args.output)]
    if args.setup:
        command.append("/resource:" + str(args.setup.resolve()) + ",SetupPayload")
    command.append(str(ROOT / "installer/InstallGate.cs"))
    subprocess.run(command, check=True)
    print(args.output)


if __name__ == "__main__":
    main()
