# Inventory Testing Guide

## Scope

Validate stock unit conversion, valuation, movement history, and COGS.

## Required Checks

- Purchase unit stored on purchase line
- Base stock unit stored on stock movement
- Valuation layer stored in base unit
- Unit cost calculated in base unit
- Sale reduces stock from base unit
- COGS calculated from valuation layer
- Negative stock rejected unless enabled
- No mixed-unit stock balances
- Tenant isolation
- Audit log for adjustments

## BOX To PCS Scenario

```text
Purchase 1 BOX of Coca Cola.
1 BOX = 12 PCS.
Purchase cost = AED 120.

Validate:
- Purchase line stores 1 BOX.
- Stock movement stores 12 PCS.
- Inventory valuation layer stores 12 PCS.
- Unit cost becomes AED 10 per PCS.
- Selling 2 PCS reduces stock correctly.
- Remaining stock = 10 PCS.
- COGS = AED 20.

Ensure stock never stores mixed units.
```
