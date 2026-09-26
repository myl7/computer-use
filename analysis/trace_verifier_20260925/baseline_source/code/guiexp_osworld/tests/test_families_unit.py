"""LLM-free family self-tests: parameter generators + checkers, host-side.

Builds the ground-truth file for an injected binding with odfpy (no guest, no
LLM), runs the family checker over it (must pass), then over a mutated copy
(must fail). This is the P1.1 criterion "checker 可判", proven without any API.
"""

import io
import random
import sys

sys.path.insert(0, ".")

from guiexp_osworld import families  # noqa: E402


def build_ods(binding: dict) -> bytes:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableRow, TableCell
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    table = Table(name="Sheet1")
    grid = [
        [binding["header_a"], binding["header_b"]],
        [str(int(binding["val_a2"])), str(int(binding["val_b2"]))],
        [str(int(binding["val_a3"])), str(int(binding["val_b3"]))],
    ]
    for row_values in grid:
        row = TableRow()
        for value in row_values:
            cell = TableCell()
            cell.addElement(P(text=value))
            row.addElement(cell)
        table.addElement(row)
    doc.spreadsheet.addElement(table)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_odt(binding: dict) -> bytes:
    from odf.opendocument import OpenDocumentText
    from odf.text import P

    doc = OpenDocumentText()
    for text in (binding["title"], "", binding["body"]):
        doc.text.addElement(P(text=text))
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def main() -> int:
    failures = 0

    # -- determinism: same seed -> same instance, across families
    for family in families.FAMILIES:
        a = families.instance_params(family, 7)
        b = families.instance_params(family, 7)
        c = families.instance_params(family, 8)
        assert a == b, f"{family}: seed 7 not deterministic"
        assert a != c, f"{family}: seed 8 collided with seed 7"
        goal = families.goal_text(family, 7, a)
        goal2 = families.goal_text(family, 7, a)
        assert goal == goal2, "goal phrasing not seed-deterministic"
        for field in families.binding_fields(family):
            assert str(a[field]).strip(), f"{family}.{field} empty"
            value = str(a[field])
            assert value in goal, f"{family}.{field}={value} missing from goal"
        print(f"OK gen {family}: {a['file_name']}, {len(goal)}-char goal")

    # -- checker positive: ground-truth file passes
    calc_binding = families.instance_params("CalcTableSave", 3)
    ok, err = families.check_calc(calc_binding, build_ods(calc_binding))
    print("OK checker calc positive:", ok, err)
    failures += 0 if ok else 1

    writer_binding = families.instance_params("WriterMemoSave", 3)
    ok, err = families.check_writer(writer_binding, build_odt(writer_binding))
    print("OK checker writer positive:", ok, err)
    failures += 0 if ok else 1

    # -- checker negatives: each single-field mutation must fail
    wrong = dict(calc_binding, val_b2=int(calc_binding["val_b2"]) + 1)
    ok, err = families.check_calc(wrong, build_ods(calc_binding))
    print("OK checker calc wrong-binding:", not ok, err)
    failures += 0 if (not ok) else 1

    wrong_file = build_ods(dict(calc_binding, header_b="Nope"))
    ok, err = families.check_calc(calc_binding, wrong_file)
    print("OK checker calc wrong-file:", not ok, err)
    failures += 0 if (not ok) else 1

    wrong = dict(writer_binding, body=writer_binding["body"] + " Indeed.")
    ok, err = families.check_writer(wrong, build_odt(writer_binding))
    print("OK checker writer wrong-binding:", not ok, err)
    failures += 0 if (not ok) else 1

    # middle line not empty
    from odf.opendocument import OpenDocumentText
    from odf.text import P
    doc = OpenDocumentText()
    for text in (writer_binding["title"], " ", writer_binding["body"]):
        doc.text.addElement(P(text=text))
    buffer = io.BytesIO()
    doc.save(buffer)
    ok, err = families.check_writer(writer_binding, buffer.getvalue())
    print("OK checker writer middle-not-empty:", not ok, err)
    failures += 0 if (not ok) else 1

    # garbage bytes
    ok, err = families.check_calc(calc_binding, b"not an ods")
    print("OK checker calc garbage:", not ok, err)
    failures += 0 if (not ok) else 1

    # -- goal template round trip (the compiler's family header)
    template = families.goal_template_text(
        "CalcTableSave", families.goal_text("CalcTableSave", 3, calc_binding), calc_binding)
    assert "{" in template and "}" in template, "template has no placeholders"
    assert str(calc_binding["file_name"]) not in template
    print("OK goal template:", template[:100].replace(chr(10), " ") + "...")

    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
