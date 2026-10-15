"""VAT review: find duplicate bills (and later, odd VAT) in an organization's books.

Code finds the problems from the posted bills and says exactly why; the model only explains them. Read-only: it
queries the governed `purchases` view and adds no table. Plan and data findings: docs/VAT_REVIEW_PLAN.md.
"""
