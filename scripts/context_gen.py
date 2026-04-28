import os
from pathlib import Path

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".idea",
    ".vscode",
    "alembic",
    "logs",
    "data",
    "build",
    "dist",
    "node_modules",
    "artifacts",
    "tmp_outputs",
    "certbot",
    ".code-review-graph",
    "AmaExecutionCore.egg-info",
}
INCLUDE_EXT = {".py", ".toml", ".md", ".yml", ".yaml", ".ts", ".tsx", ".json", ".ini"}
IGNORE_FILES = {
    "data.sqlite",
    "history.db",
    "uv.lock",
    "yarn.lock",
    "package-lock.json",
    "poetry.lock",
    "vite-env.d.ts",
}


def generate_context() -> None:
    output_path = Path("ama_execution_core_context.txt")

    with output_path.open("w", encoding="utf-8") as outfile:
        for root, dirs, files in os.walk("."):
            dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
            root_path = Path(root)

            for file in sorted(files):
                if file in IGNORE_FILES:
                    continue

                path = root_path / file
                ext = path.suffix
                if ext in INCLUDE_EXT or file == "Dockerfile":
                    outfile.write(f"\n{'=' * 20}\nFILE: {path}\n{'=' * 20}\n")

                    try:
                        with path.open(encoding="utf-8") as infile:
                            outfile.write(infile.read())
                    except Exception as e:
                        outfile.write(f"Error reading file: {e}")

    print(f"Ready. File {output_path} created.")


if __name__ == "__main__":
    generate_context()
