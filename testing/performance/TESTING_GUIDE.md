# Performance Testing Guide

## Scope

Validate API, posting, reporting, inventory valuation, and queue behavior under load.

## Required Checks

- 100 concurrent invoice postings
- Queue worker stability
- Database response time
- Report generation speed
- Inventory valuation performance
- API response time under load
- Duplicate/idempotency behavior under concurrent requests
- Tenant isolation under concurrent mixed-tenant requests

## Recommended Tools

- k6
- Locust
- JMeter

## Agent Prompt

```text
Run load testing.

Validate:
- 100 concurrent invoice postings
- Queue worker stability
- Database response time
- Report generation speed
- Inventory valuation performance
- API response under load
```
