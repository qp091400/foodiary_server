"""Create a private production env file without displaying or overwriting secrets."""

import argparse
import os
import re
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True, help="Public DNS name pointing to the server")
    parser.add_argument("--email", required=True, help="TLS certificate contact email")
    parser.add_argument("--output", type=Path, default=Path(".env.production"))
    args = parser.parse_args()
    domain = args.domain.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain) or "." not in domain:
        parser.error("--domain must be a DNS hostname, without https:// or a path")
    if "@" not in args.email or any(c.isspace() for c in args.email):
        parser.error("--email must be a contact email address")
    template = Path(__file__).resolve().parents[1] / "deploy" / ".env.production.example"
    replacements = {
        "API_DOMAIN": domain,
        "ACME_EMAIL": args.email,
        "POSTGRES_PASSWORD": secrets.token_urlsafe(36),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "CORS_ORIGINS": f"https://{domain}",
        "TRUSTED_HOSTS": f"{domain},localhost,127.0.0.1",
    }
    lines = []
    for line in template.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={replacements[key]}" if key in replacements else line)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(lines) + "\n")
    print(f"Created {args.output}. Fill in SMTP_USER, SMTP_PASSWORD and MAIL_FROM before starting.")
    print("Secrets were generated locally and have not been printed. Keep this file private.")


if __name__ == "__main__":
    main()
