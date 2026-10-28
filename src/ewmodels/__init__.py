"""ewmodels — open, auditable energy-analytics models from Enerwyse.

Every model returns a :class:`~ewmodels.receipts.Result`: the computed value
plus a :class:`~ewmodels.receipts.Receipt` recording what went in, which checks
passed, and which caveats apply. Numbers without evidence are not results.
"""

from ewmodels.receipts import Check, Receipt, Result

__version__ = "0.4.0"
__all__ = ["Check", "Receipt", "Result", "__version__"]
