# Payroll Testing Guide

## Scope

Validate payroll workflow controls before payroll, WPS, and SIF features are expanded.

## Required Checks

- Payroll permissions
- Employee tenant isolation
- Payroll run approval
- Duplicate payroll run prevention
- Journal posting balance
- WPS/SIF generation controls where implemented
- Locked period rejection
- Audit log for run creation, approval, posting, and export

## Accounting Rule

Payroll posting must create balanced journals and must not bypass approval or period controls.
