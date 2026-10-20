"""Accept Agent Skills space-separated tools in Cisco scanner 2.0.11.

Patch only the pinned parser; leave skill sources and security rules unchanged.
Keep comma support and spaces/commas inside scoped tool arguments. Fail the
build if upstream changes, so a scanner upgrade requires reviewing this patch.
"""

from importlib.metadata import version
from pathlib import Path

OLD = """            # Agent skill docs commonly show comma-separated tool lists in YAML frontmatter
            # (e.g., "allowed-tools: Read, Grep, Glob"). Treat this as a list.
            parts = [p.strip() for p in self.allowed_tools.split(",")]
            self.allowed_tools = [p for p in parts if p]
"""

NEW = """            # Agent Skills uses spaces; also accept legacy comma separators.
            # Scoped tools can contain either delimiter: Bash(git status:*).
            parts = []
            start = 0
            depth = 0
            for index, char in enumerate(self.allowed_tools):
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth = max(0, depth - 1)
                elif depth == 0 and (char.isspace() or char == ","):
                    part = self.allowed_tools[start:index].strip()
                    if part:
                        parts.append(part)
                    start = index + 1
            part = self.allowed_tools[start:].strip()
            if part:
                parts.append(part)
            self.allowed_tools = parts
"""


def patch(target: Path) -> None:
    source = target.read_text()
    if source.count(OLD) != 1:
        raise RuntimeError("Cisco allowed-tools parser changed; review the compatibility patch")
    target.write_text(source.replace(OLD, NEW, 1))


if __name__ == "__main__":
    if version("cisco-ai-skill-scanner") != "2.0.11":
        raise RuntimeError("Review the allowed-tools patch before upgrading Cisco scanner")
    from skill_scanner.core import models

    patch(Path(models.__file__))
