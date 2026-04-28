import os

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
    output_file = "ama_execution_core_context.txt"

    with open(output_file, "w", encoding="utf-8") as outfile:
        for root, dirs, files in os.walk("."):
            dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)

            for file in sorted(files):
                if file in IGNORE_FILES:
                    continue

                _, ext = os.path.splitext(file)
                if ext in INCLUDE_EXT or file == "Dockerfile":
                    path = os.path.join(root, file)

                    outfile.write(f"\n{'=' * 20}\nFILE: {path}\n{'=' * 20}\n")

                    try:
                        with open(path, encoding="utf-8") as infile:
                            outfile.write(infile.read())
                    except Exception as e:
                        outfile.write(f"Error reading file: {e}")

    print(f"Ready. File {output_file} created.")


if __name__ == "__main__":
    generate_context()
