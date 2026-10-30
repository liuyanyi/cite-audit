"""Run the packaged application locally; no development server is required."""
import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Cite Audit local single-user app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=os.getenv("CITE_AUDIT_DATA_DIR", ".cite-audit-data"))
    args = parser.parse_args()
    os.environ["CITE_AUDIT_DATA_DIR"] = args.data_dir
    uvicorn.run("cite_audit.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
