"""Guard the parser compatibility change without accepting invalid HCL."""

import pytest
from lark.exceptions import UnexpectedInput

from tests.helpers.hcl_compat import loads

pytestmark = pytest.mark.infrastructure


def test_multiline_traversal_preserves_literals_and_expression():
    multiline = '''value = (
  module.encounter
  # Continued traversal, accepted by Terraform.
  .table_name
)
description = "literal\\n .unchanged"
body = <<-TEXT
module.literal
.unchanged
TEXT
'''
    single_line = multiline.replace(
        "module.encounter\n  # Continued traversal, accepted by Terraform.\n  .table_name",
        "module.encounter.table_name",
    )
    assert loads(multiline) == loads(single_line)


def test_invalid_traversals_are_not_accepted():
    for source in ("value = (module.encounter\n .)", "value = module.encounter\n .table_name"):
        with pytest.raises(UnexpectedInput):
            loads(source)
