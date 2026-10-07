"""Safe Decimal evaluator and input resolution. Standard library only."""
from __future__ import annotations
import ast
import re
from decimal import Decimal, getcontext, ROUND_HALF_UP
from .numbers import (extract, parse_block, currency_code, value_scale, value_currency, currency_ok, stated_as, digits,
                      ParsedBlock, UNIT_NAMES, SYMBOLS, _CODES, _SCALES)

getcontext().prec = 28
_SCALE_EXP = {None: 0, "": 0, "k": 3, "thousand": 3, "m": 6, "mn": 6, "million": 6, "bn": 9, "billion": 9}
FORMATS = ("pct", "num", "money")
UNITS = ("x", "p", "c", "kg", "t", "TEU", "m3", "pcs", "pp")
_LITERALS = frozenset(Decimal(i) for i in range(13)) | {Decimal("0.5")}
_PCT_SCALERS = frozenset({Decimal(100), Decimal("0.01")})
_MAX_EXPONENT = 100
_MAX_LENGTH = 500
PCT_HINT = "format 'pct' already multiplies by 100: give the ratio (for example op / rev) and do not multiply by 100"
SCALE_HINT = ("set the figure's scale (k, m, bn) instead of multiplying: a figure in another scale needs no calculation, so "
              "cite its block and write it in the new scale (for example $20.3bn for 20,269 in a $ million table)")
_SYNTAX = "+ - * / ** ( ) and min, max, abs, round"
# Units the model may pass, with what they mean; 'pp' is accepted with format pct but never suggested.
_UNIT_MEANING = {"x": "multiple", "p": "pence", "c": "cents", "kg": "kilograms", "t": "tonnes", "TEU": "containers",
                 "m3": "cubic metres", "pcs": "pieces"}
_UNIT_CANON = {u.lower(): u for u in UNITS} | {"cents": "c", "cent": "c", "pence": "p", "kgs": "kg", "tonnes": "t"}
_PERCENT_WORDS = ("%", "pct", "percent")
_MONEY_UNIT = re.compile(rf"^(?:(?P<code>{_CODES})|(?P<sym>US\$|[£$€]))?\s?(?P<scale>{'|'.join(sorted(_SCALES, key=len, reverse=True))})?$",
                         re.I)
_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}
_SUFFIX = {0: "", 3: "k", 6: "m", 9: "bn", 12: "tn"}

class CalcError(Exception):
    pass

_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}
_FUNCS = {"min": (min, 2, 12), "max": (max, 2, 12), "abs": (abs, 1, 1), "round": (None, 1, 2)}

def _parse(expression: str) -> ast.Expression:
    if not isinstance(expression, str) or not expression.strip():
        raise CalcError("expression is empty")
    if len(expression) > _MAX_LENGTH:
        raise CalcError(f"expression too long (at most {_MAX_LENGTH} characters)")
    try:
        return ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"syntax error: {e.msg.split('.')[0]}") from None
    except (RecursionError, MemoryError, ValueError):
        raise CalcError("expression too deeply nested") from None

def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def _is_rescale(d: Decimal) -> bool:
    """A power of ten of 1000 or more, or 0.001 or less: a change of scale, not a calculation."""
    if d <= 0:
        return False
    k = d.log10()
    return abs(k - k.to_integral_value()) < Decimal("1e-9") and abs(k) >= 3

def _literal_message(values: list) -> str:
    """One message for every disallowed literal; multiplying by 100 (or 0.01) also gets the pct hint, and by a power of
    ten such as 1000 the scale hint (seen in a run: "x / 1000" was followed by a "thousand" assumption and $0.0203bn)."""
    shown = list(dict.fromkeys(repr(v) for v in values))
    names = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]
    msg = (f"literal{'s' if len(shown) > 1 else ''} {names} not allowed: an expression may only contain the integers 0 to 12 "
           "and 0.5; write input names, not values, and pass any other number as an input from its block")
    if any(Decimal(str(v)) in _PCT_SCALERS for v in values):
        return msg + ". " + PCT_HINT
    if any(_is_rescale(abs(Decimal(str(v)))) for v in values):
        return msg + ". " + SCALE_HINT
    return msg + " (or as an assumption with a reason)"

def _literal(n: ast.Constant) -> Decimal:
    v = n.value
    if not _is_number(v):
        raise CalcError(f"literal {v!r} not allowed: an expression may only contain input names and the integers 0 to 12 and 0.5")
    d = Decimal(str(v))
    if d not in _LITERALS:
        raise CalcError(_literal_message([v]))
    return d

def literal_problems(expression: str | ast.AST) -> list[str]:
    """Every number in the expression that is not 0 to 12 or 0.5, in the order written, as one message."""
    tree = expression if isinstance(expression, ast.AST) else _parse(expression)
    consts = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Constant) and _is_number(n.value)),
                    key=lambda n: (n.lineno, n.col_offset))
    bad = [n.value for n in consts if Decimal(str(n.value)) not in _LITERALS]
    return [_literal_message(bad)] if bad else []

def _eval(n: ast.AST, env: dict[str, Decimal], flags: dict) -> Decimal:
    if isinstance(n, ast.Constant):
        if flags.get("any_literal") and _is_number(n.value):
            return Decimal(str(n.value))                         # pct_guard sizes constants it will reject anyway
        return _literal(n)
    if isinstance(n, ast.Name):
        if n.id not in env:
            raise CalcError(f"unknown name '{n.id}'")
        return env[n.id]
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
        v = _eval(n.operand, env, flags)
        return -v if isinstance(n.op, ast.USub) else v
    if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
        a, b = _eval(n.left, env, flags), _eval(n.right, env, flags)
        if isinstance(n.op, ast.Div) and b == 0:
            raise CalcError("division by zero")
        return _BIN[type(n.op)](a, b)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
        a, b = _eval(n.left, env, flags), _eval(n.right, env, flags)
        if abs(b) > _MAX_EXPONENT:
            raise CalcError(f"exponent {b} is too large")
        if a == 0 and b <= 0:
            raise CalcError("zero raised to a zero or negative power")
        if b == b.to_integral_value():
            return a ** int(b)
        if a < 0:
            raise CalcError("negative base with fractional exponent")
        flags["float"] = True
        return Decimal(repr(float(a) ** float(b)))
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
        fn, lo, hi = _FUNCS[n.func.id]
        if not lo <= len(n.args) <= hi:
            raise CalcError(f"{n.func.id}() takes {lo} to {hi} arguments")
        args = [_eval(a, env, flags) for a in n.args]
        if n.func.id == "round":
            digits = args[1] if len(args) > 1 else Decimal(0)
            if digits != digits.to_integral_value() or abs(digits) > 12:
                raise CalcError("round() takes a whole number of digits from -12 to 12")
            return round(args[0], int(digits))
        return fn(*args)
    raise CalcError(f"disallowed expression element: {type(n).__name__}")

def _run(node: ast.AST, env: dict[str, Decimal], flags: dict) -> Decimal:
    try:
        result = _eval(node, env, flags)
    except CalcError:
        raise
    except RecursionError:
        raise CalcError("expression too deeply nested") from None
    except (ArithmeticError, ValueError, TypeError) as e:
        raise CalcError(f"arithmetic error ({type(e).__name__})") from None
    if not result.is_finite():
        raise CalcError("result is not a finite number")
    return result

def evaluate(expression: str, env: dict[str, Decimal]) -> tuple[Decimal, bool]:
    flags = {"float": False}
    result = _run(_parse(expression).body, env, flags)
    return result, flags["float"]

def _names(tree: ast.AST) -> set[str]:
    funcs = {id(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in funcs}

def _constant_factor(tree: ast.AST, consts: dict[str, Decimal]) -> Decimal:
    """The product of an expression's constant factors (literals and the named constants), outside exponents."""
    product = [Decimal(1)]

    def walk(n: ast.AST, inverse: bool) -> None:
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Div)):
            for side, right in ((n.left, False), (n.right, True)):
                inv = inverse ^ (right and isinstance(n.op, ast.Div))
                names = _names(side)
                if names and not names <= consts.keys():
                    walk(side, inv)
                else:
                    v = abs(_run(side, consts, {"float": False, "any_literal": True}))
                    if v:
                        product[0] = product[0] / v if inv else product[0] * v
        elif isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)):
            walk(n.left, inverse)
            walk(n.right, inverse)
        elif isinstance(n, ast.UnaryOp):
            walk(n.operand, inverse)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            for a in (n.args[:1] if n.func.id == "round" else n.args):
                walk(a, inverse)
        # powers are left alone: constants inside exponents do not scale the result

    walk(tree, False)
    return product[0]

def pct_guard(expression: str, consts: dict[str, Decimal] | None = None) -> None:
    """Format pct multiplies by 100 itself: reject constant factors of 100 or more (or 1/100 or less), e.g. * 10 * 10.

    `consts` are the assumption inputs (resolved values): a factor built only from them counts as a constant, so an
    assumption 'hundred = 100' is caught like the literal 100."""
    if abs(_constant_factor(_parse(expression).body, consts or {}).log10()) >= 2:
        raise CalcError(PCT_HINT)

def pp_guard(expression: str, percents: set[str]) -> None:
    """A sum or difference of percentages is itself a percentage: with format num, 75.0% - 74.0% showed as 0.0100
    (live run n4, 7 Oct). A ratio of percentages stays allowed."""
    tree = _parse(expression)
    ops = [n for n in ast.walk(tree) if isinstance(n, ast.BinOp)]
    if ops and all(isinstance(n.op, (ast.Add, ast.Sub)) for n in ops) and _names(tree) <= percents:
        raise CalcError("these inputs are percentages and you add or subtract them: use format 'pct' (with unit 'pp' "
                        "when the question asks for percentage points)")

def scale_guard(expression: str, consts: dict[str, Decimal] | None = None) -> None:
    """Refuse a calculation whose only role is a power-of-ten change of scale of one input (x / 1000, x / 10 / 10 / 10,
    x / thousand with an assumption 'thousand = 1000'). Seen in runs: the model spent its turns on such calculations
    ($0.0203bn, 20,269,000) instead of writing $20.3bn next to the block."""
    consts = consts or {}
    tree = _parse(expression).body
    funcs = {id(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
    names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in funcs and n.id not in consts]
    if len(names) != 1:
        return
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and not isinstance(n.op, (ast.Mult, ast.Div)):
            return
        if isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id == "abs"):
            return
    if _is_rescale(abs(_constant_factor(tree, consts))):
        raise CalcError(SCALE_HINT)

def period_guard(expression: str, years: dict[str, int], consts: dict[str, Decimal] | None = None) -> None:
    """A growth rate's root must match the years its inputs span: (r24 / r22) ** (1/3) - 1 over 2022 to 2024 is refused.

    `years` are the column years of the document inputs; `consts` the assumption inputs (n = 3 in ** (1 / n)).
    Seen in a run: a "3-year CAGR" over the 2022 and 2024 columns was computed with 1/3 and passed every check."""
    consts = consts or {}
    for node in ast.walk(_parse(expression).body):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow)) or _names(node.right) - consts.keys():
            continue
        try:
            e = abs(_run(node.right, consts, {"float": False, "any_literal": True}))
        except CalcError:
            continue
        if not 0 < e < 1 or abs(1 / e - (1 / e).to_integral_value()) > Decimal("0.001"):
            continue
        n = int((1 / e).to_integral_value())
        seen = sorted({years[x] for x in _names(node.left) if x in years})
        span = seen[-1] - seen[0] if len(seen) > 1 else None
        if span and span != n:
            raise CalcError(f"the exponent uses 1/{n} but the inputs span {span} year{'s' if span > 1 else ''} "
                            f"({seen[0]} to {seen[-1]}): a compound annual growth rate over them is "
                            f"(end / start) ** (1 / {span}) - 1")

def input_problems(inputs: list[dict], expression: str | ast.AST) -> list[str]:
    """Names, duplicates, unused inputs and the document rule, all at once."""
    if not isinstance(inputs, list) or not all(isinstance(i, dict) for i in inputs):
        return ["inputs must be a list of objects"]
    tree = expression if isinstance(expression, ast.AST) else _parse(expression)
    names = [i.get("name") for i in inputs]
    valid = [n for n in names if isinstance(n, str) and n.isidentifier()]
    out = [f"input name {n!r} must be a simple name such as rev_2024" for n in names if n not in valid]
    seen: set[str] = set()
    for n in valid:
        if n in seen:
            out.append(f"duplicate input name '{n}'")
        seen.add(n)
    used = _names(tree)
    out += [f"input '{n}' is not used in the expression" for n in dict.fromkeys(valid) if n not in used]
    if not any(i.get("source_id") != "assumption" for i in inputs):
        out.append("at least one input must come from a document; pass figures from a block with its source_id")
    return out

def check_inputs(inputs: list[dict], expression: str) -> None:
    problems = input_problems(inputs, expression)
    if problems:
        raise CalcError("; ".join(problems))

def _slug(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower()
    return s if s and not s[0].isdigit() else f"x_{s}"

def static_problems(inputs: list[dict], expression: str) -> list[str]:
    """Everything that can be said before resolving inputs, so the model can fix it all in one retry."""
    try:
        tree = _parse(expression)
    except CalcError as e:
        if not str(e).startswith("syntax error"):
            return [str(e)]
        names = [i.get("name") for i in inputs if isinstance(i, dict)] if isinstance(inputs, list) else []
        bad = {n: _slug(n) for n in names if isinstance(n, str) and not n.isidentifier()}
        if bad:                                                  # seen in a run: names with spaces ("Operating profit FY2024")
            fixed = expression
            for old in sorted(bad, key=len, reverse=True):
                fixed = fixed.replace(old, bad[old])
            renames = " and ".join(f"'{o}' to {s}" for o, s in bad.items())
            return [f"syntax error in the expression: input names must be simple names (letters, digits and _): rename "
                    f"{renames}, and write the expression as {fixed}"]
        known = ", ".join(n for n in names if isinstance(n, str))
        return [f"syntax error in the expression ({str(e).removeprefix('syntax error: ')}): write it with the input names "
                f"({known}), {_SYNTAX}"]
    return input_problems(inputs, tree) + literal_problems(tree)

def parse_money_unit(unit) -> tuple[str | None, int | None] | None:
    """'GBPm' -> ('GBP', 6), '£m' -> ('GBP', 6), 'm' -> (None, 6), 'USD' -> ('USD', None); None if not money."""
    if not isinstance(unit, str):
        return None
    m = _MONEY_UNIT.match(unit.strip())
    if not m or not (m.group("code") or m.group("sym") or m.group("scale")):
        return None
    scale = m.group("scale")
    return currency_code(m.group("code") or m.group("sym")), (_SCALES[scale.lower()] if scale else None)

def resolve_unit(fmt: str, unit) -> tuple[str | None, tuple | None, str | None]:
    """(unit to record, (currency, scale) to show money in, problem). Percent words are ignored with format pct."""
    if unit is None or (isinstance(unit, str) and not unit.strip()):
        return None, None, None
    if not isinstance(unit, str):
        return None, None, f"unit must be text, not {unit!r}"
    u = unit.strip()
    canon = _UNIT_CANON.get(u.lower())
    meaning = f" ({_UNIT_MEANING[canon]})" if canon in _UNIT_MEANING else ""
    if fmt == "pct":
        if u.lower() in _PERCENT_WORDS:
            return None, None, None
        if canon == "pp":
            return "pp", None, None                              # only when the model asks for percentage points
        return None, None, f"unit '{u}'{meaning} does not go with format 'pct': for a percentage leave unit out"
    if u.lower() in _PERCENT_WORDS:
        return None, None, f"unit '{u}' needs format 'pct': give the ratio (for example op / rev) and set format to pct"
    if canon == "pp":
        return None, None, "unit 'pp' needs format 'pct'"
    if canon:
        return canon, None, None
    money = parse_money_unit(u)
    if money:
        return None, money, None
    known = ", ".join(f"{k} ({v})" for k, v in _UNIT_MEANING.items())
    return None, None, f"unit '{u}' is not a known unit: use {known}, a currency and scale such as GBPm for money, or leave it out"

def money_dim(expression: str | ast.AST, dims: dict[str, int | None]) -> int | None:
    """Power of money in the result: 1 for an amount, 0 for a ratio, None when unknown (mixed, or an unknown input)."""
    tree = expression if isinstance(expression, ast.AST) else _parse(expression)

    def d(n: ast.AST) -> int | None:
        if isinstance(n, ast.Expression):
            return d(n.body)
        if isinstance(n, ast.Constant):
            return 0
        if isinstance(n, ast.Name):
            return dims.get(n.id)
        if isinstance(n, ast.UnaryOp):
            return d(n.operand)
        if isinstance(n, ast.BinOp):
            a, b = d(n.left), d(n.right)
            if isinstance(n.op, (ast.Add, ast.Sub)):
                return a if a == b else None
            if a is None or b is None:
                return None
            if isinstance(n.op, ast.Mult):
                return a + b
            if isinstance(n.op, ast.Div):
                return a - b
            return 0 if a == b == 0 else None                    # a power of an amount is not an amount
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            args = [d(x) for x in (n.args[:1] if n.func.id == "round" else n.args)]
            return args[0] if args and all(x == args[0] for x in args) else None
        return None

    return d(tree)

def _base(value: Decimal, neg: bool, exp: int, percent: bool, unit: str | None) -> Decimal:
    if percent:
        v = value / (10000 if unit == "bps" else 100)           # 24.0% -> 0.24
    else:
        v = value * (Decimal(10) ** exp)
    return -v if neg else v

def resolve_input(inp: dict, block: str | ParsedBlock | None) -> tuple[Decimal, dict]:
    name, shown, sid = inp.get("name", "?"), inp.get("value"), inp.get("source_id")
    if sid in (None, ""):
        raise CalcError(f"input '{name}' has no source_id: give its block ID (for example D1:p1:b3), or 'assumption' with a reason")
    figs = extract(str(shown), apply_exemptions=False) if shown not in (None, "") else []
    if len(figs) != 1 or figs[0].unparsed:
        raise CalcError(f"input '{name}': value '{shown}' is not one number written as in the block "
                        "(for example 2,861 or (3,300) or 103.6p or 24.0%)")
    f = figs[0]
    declared = str(inp.get("scale") or "").lower() or None
    if declared not in _SCALE_EXP:
        raise CalcError(f"input '{name}': unknown scale '{declared}' (use k, m, bn or omit)")
    if sid == "assumption":
        # 0 to 12 or 0.5 with nothing attached is a constant, like the same number written in the expression
        constant = f.value in _LITERALS and not (f.is_percent or f.unit or f.currency or f.scale or declared)
        if not constant and not inp.get("reason"):
            raise CalcError(f"input '{name}': an assumption needs a reason")
        exp = 0 if (f.is_percent or f.unit) else (f.scale or _SCALE_EXP[declared])
        return _base(f.value, f.neg, exp, f.is_percent, f.unit), {
            "matched": None, "block_scale": exp, "span": None, "unit": f.unit, "percent": f.is_percent,
            "constant": constant, "currency": None if (f.is_percent or f.unit) else currency_code(f.currency),
            "row": None, "in_table": False, "year": None}
    if block is None:
        raise CalcError(f"input '{name}': unknown source_id '{sid}'")
    pb = block if isinstance(block, ParsedBlock) else parse_block(block)
    same = [c for c in pb.candidates if c.value == f.value]
    if not same:
        found = ", ".join(sorted({c.raw.strip() for c in pb.candidates})[:15])
        raise CalcError(f"input '{name}': {shown} not found in {sid}. Figures there: {found}")
    cands = [c for c in same if c.is_percent == f.is_percent]
    if not cands:
        c = same[0]
        if c.is_percent:
            raise CalcError(f"input '{name}': {shown} is a percentage in {sid} ('{c.raw}'); pass it with the % sign "
                            "and it is used as a ratio (24.0% = 0.24)")
        raise CalcError(f"input '{name}': {shown} is not a percentage in {sid} ('{c.raw}'); remove the % sign")
    if not f.is_percent and (f.unit or f.currency or f.scale):
        ok = [c for c in cands if c.unit == f.unit]              # 173.2p is not 173.2 cents; £103.6 is not 103.6p
        if not ok:
            c = cands[0]
            have = (f"is in {UNIT_NAMES.get(f.unit, f.unit)}" if f.unit else
                    f"is an amount in {SYMBOLS.get(currency_code(f.currency), str(f.currency).strip())}" if f.currency else
                    "is an amount")
            raise CalcError(f"input '{name}': {shown} {have} but {sid} shows {digits(c.value, c.decimals)} in "
                            f"{stated_as(c, pb)}; pass it as written in the block ({c.raw.strip()})")
        cands = ok
    if f.neg:
        cands = [c for c in cands if c.neg]
        if not cands:
            raise CalcError(f"input '{name}': {shown} is negative but {sid} shows it as positive ('{same[0].raw}')")
    fcur = currency_code(f.currency)
    if fcur:
        ok = [c for c in cands if currency_ok(fcur, c, pb)]
        if not ok:
            c, cur = cands[0], str(f.currency).strip()
            bcur = value_currency(c, pb)
            raise CalcError(f"input '{name}': {shown} has currency {cur} but {sid} is in {SYMBOLS.get(bcur, bcur)}" if bcur else
                            f"input '{name}': {shown} has currency {cur} but {sid} shows {digits(c.value, c.decimals)} in "
                            f"{stated_as(c, pb)}, with no currency")
        cands = ok
    c = cands[0]
    unit = f.unit or c.unit
    if f.is_percent or unit:
        exp = 0                                                 # percentages and unit figures never take a header scale
    else:
        exp_block = value_scale(c, pb)
        if f.scale and exp_block and f.scale != exp_block:
            raise CalcError(f"input '{name}': the scale of {shown} disagrees with the block's scale (10^{exp_block})")
        if not f.scale and declared is not None and exp_block and _SCALE_EXP[declared] != exp_block:
            raise CalcError(f"input '{name}': declared scale '{declared}' disagrees with the block's scale (10^{exp_block})")
        exp = exp_block or f.scale or _SCALE_EXP[declared]
    money = None if (f.is_percent or unit) else (fcur or value_currency(c, pb))
    years = {pb.col_years.get(x.col) for x in cands}
    return _base(f.value, f.neg, exp, f.is_percent, f.unit), {
        "matched": c.raw.strip(), "block_scale": exp, "span": [c.start, c.end], "unit": unit, "percent": f.is_percent,
        "constant": False, "currency": money, "row": c.label, "in_table": c.col is not None,
        "year": years.pop() if len(years) == 1 else None}

def _fixed(v: Decimal, places: int) -> str:
    return f"{v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP):,f}"

def _sig3(v: Decimal) -> int:
    """Decimal places that show 3 significant digits without rounding the integer part."""
    return 0 if v == 0 else max(0, 2 - v.adjusted())

def _num(v: Decimal) -> str:
    return _fixed(v, 0) if v == v.to_integral_value() else _fixed(v, _sig3(v))

def format_result(value: Decimal, fmt: str, unit: str | None = None,
                  money: tuple[str | None, int] | None = None) -> tuple[str, bool]:
    """Display text and percent flag. `money` = (currency, scale): an amount shown as -£19m (the value stays in base units)."""
    if fmt not in FORMATS:
        raise CalcError(f"format must be pct, num or money, not '{fmt}'")
    if fmt == "pct":
        v = value * 100
        return _fixed(v, max(1, _sig3(v))) + ("pp" if unit == "pp" else "%"), True
    if money is not None:
        cur, exp = money
        v = value / (Decimal(10) ** exp)
        shown = _fixed(abs(v), 2) if fmt == "money" else _num(abs(v))
        prefix = _SYMBOL.get(cur, f"{cur} " if cur else "")
        return ("-" if v < 0 else "") + prefix + shown + _SUFFIX.get(exp, ""), False
    if fmt == "money":
        shown = _fixed(value, 2)
    else:
        shown = _num(value)
    return shown + ("" if not unit else unit if unit in ("x", "p", "c") else f" {unit}"), False
