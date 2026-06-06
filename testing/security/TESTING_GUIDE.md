# Security Testing Guide

## Scope

Validate SaaS accounting security, authorization, tenant isolation, upload controls, and sensitive audit trails.

## Required Checks

- User cannot access another tenant's data
- JWT validation
- Session expiration
- Permission checks for sensitive actions
- API authorization on every write endpoint
- SQL injection prevention
- File upload type and size validation
- Upload scanning hook or production gap noted
- Audit logging for sensitive actions
- Secrets are not exposed to frontend bundles

## Agent Prompt

```text
Test security.

Validate:
- User cannot access another tenant data.
- JWT validation.
- Permission checks.
- SQL injection prevention.
- File upload validation.
- API authorization.
- Audit logging for sensitive actions.
- Session expiration.
```
