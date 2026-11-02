"""Narrow python-hcl2 8.1.2 compatibility for parenthesized traversals.

Terraform permits newlines before traversal dots inside parentheses. The
upstream Lark postlexer handles continued operators but not traversal dots.
Keep the upstream grammar and transformation, adjusting only those whitespace
tokens. Strings/heredocs are lexer tokens and are never rewritten as text.
Terraform fmt/validate tests remain the authoritative syntax checks.
"""

from functools import lru_cache
from pathlib import Path

import hcl2
from hcl2.postlexer import PostLexer
from lark import Lark


class _TraversalPostLexer(PostLexer):
    def process(self, stream):
        def continuations():
            depth = 0
            pending = []
            for token in stream:
                if token.type == "NL_OR_COMMENT":
                    pending.append(token)
                    continue
                if not (token.type == "DOT" and depth > 0):
                    yield from pending
                pending.clear()
                if token.type == "LPAR":
                    depth += 1
                elif token.type == "RPAR":
                    depth -= 1
                yield token
            yield from pending

        yield from super().process(continuations())


@lru_cache(maxsize=1)
def _parser():
    return Lark.open(
        str(Path(hcl2.__file__).with_name("hcl2.lark")),
        parser="lalr",
        propagate_positions=True,
        postlex=_TraversalPostLexer(),
    )


def loads(text):
    return hcl2.serialize(hcl2.transform(_parser().parse(text + "\n")))
