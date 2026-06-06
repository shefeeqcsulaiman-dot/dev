# AI/OCR Testing Guide

## Scope

Validate invoice extraction, confidence scoring, validation, duplicate detection, and rejection behavior.

## Required Checks

- Invoice number extraction
- Supplier/customer name extraction
- TRN extraction
- VAT amount extraction
- Total/subtotal extraction
- Low confidence detection
- Duplicate detection
- Invalid invoice rejection
- OCR failure exception creation
- Human review workflow
- Audit log for accepted/rejected extraction

## Agent Prompt

```text
Test OCR extraction.

Validate:
- Invoice number extraction
- TRN extraction
- VAT extraction
- Low confidence detection
- Duplicate detection
- Invalid invoice rejection
```
