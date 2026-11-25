# Finance
a proposed multi-user, multi-currency personal finance management application designed to help individuals and families manage their money through automation, offline access, and 
a simple user interface.
The goal is to build a secure, scalable financial platform that starts with essential money management features and can eventually grow into a subscription-based product.
The application will provide:

Dashboard: View total net worth, income, expenses and spending.

Multiple accounts: Manage bank accounts, cash, savings and credit cards.

Transactions: Record income, expenses and transfers.

Multi-currency: Support QAR, KES, USD, EUR, GBP and other currencies.

Automation: Import bank statements, categorize transactions and detect duplicates.

Offline access: Record transactions without an internet connection and synchronize later.

Budgets: Create spending limits and monitor expenses.

Savings goals: Track savings targets and contributions.

Debt management: Track loans, balances and repayments.

Reports: Analyze spending, income and financial progress.

Shared workspaces: Allow families or multiple users to manage shared finances.

Future features: Receipt scanning and an AI financial assistant.
 Technology stack
Component

Technology

Programming language

Python 3.13

Backend

Django

API

Django REST Framework

Database

PostgreSQL

Frontend

Django Templates

Styling

Tailwind CSS

Interactivity

HTMX + Alpine.js

Charts

Chart.js

Offline storage

IndexedDB

Offline support

Progressive Web App (PWA)

Background tasks

Celery + Redis

API documentation

OpenAPI

Testing

Pytest

Deployment

Google Cloud Run

Database hosting

Google Cloud SQL
ystem architecture
The application will use a modular monolithic architecture.

Its main components will be:

User management: Registration, authentication and profiles.

Workspace management: Personal and shared financial environments.

Accounts: Bank accounts, wallets and credit cards.

Financial ledger: Accurate financial transaction recording.

Currency engine: Exchange rates and currency conversion.

Transaction management: Income, expenses and transfers.

Automation: Bank imports and automatic categorization.

Synchronization: Offline transaction storage and synchronization.

Financial analytics: Budgets, reports and savings.

Audit and security: Permissions and financial activity tracking.

5. Database design
The database will use PostgreSQL and include these principal models:

Model

Purpose

User

User authentication

Workspace

Personal or shared financial environment

Membership

Workspace roles and permissions

Currency

Supported currencies

ExchangeRate

Historical exchange rates

Account

Bank and cash accounts

Category

Income and expense categories

Transaction

Financial transactions

JournalEntry

Accounting records

JournalLine

Debit and credit entries

Budget

Spending limits

SavingsGoal

Savings targets

Loan

Debt management

RecurringRule

Repeating transactions

Receipt

Receipt storage

BankConnection

Bank integrations

ImportedTransaction

Imported bank activity

SyncDevice

Registered devices

SyncOperation

Offline synchronization

AuditEvent

Financial activity history

Important architectural decision: The double-entry ledger will be the single source of truth for financial balances and reports.

This ensures accurate accounting and prevents problems such as counting credit-card repayments or transfers as additional expenses.

6. Offline and automation functionality
PocketFinance should work even without internet access.

The process will be:

User records a transaction.

Transaction is saved locally in IndexedDB.

Transaction receives a unique UUID.

When internet returns, the application sends it to Django.

Django validates and processes the transaction.

The ledger is updated.

The device receives synchronization confirmation.

Automation will be introduced progressively:

CSV and bank statement imports.

Automatic duplicate detection.

Smart transaction categorization.

Bank API integrations where supported.

Receipt scanning and intelligent categorization in later releases.

7. First user-facing release
The initial template-based release includes email registration and login, a private
personal workspace created at signup, shared workspace setup, and a workspace-aware
dashboard.

Sprint three adds six simple destinations (Home, Activity, Accounts, Reports,
Goals, and More), owner-controlled workspace settings, and a currency catalog
seeded with QAR, KES, USD, EUR, and GBP. Add further currencies through Django
admin when needed. Accounts retain their original currency; opening balances
are converted with a cached (24-hour) quote from `open.er-api.com`, while
transaction rates are saved at posting time and used for reporting-currency
totals. If a current quote is unavailable, foreign-currency account creation
fails clearly rather than guessing.
Choose the workspace reporting currency before entering opening balances or
transactions; it is locked afterward so historical conversions remain consistent.
Income, expense, and transfer postings create balanced, immutable double-entry
journal entries. Credit-card purchases increase the card liability, while
repayments are transfers and do not inflate expense totals. Categories,
recent-transaction suggestions, reusable quick-entry templates, and account CSV
export are available in the interface. Expense refunds can link to their
original expense and reduce reported expenses rather than appearing as income.
The interface uses a navy, teal, and soft-white palette with a floating
four-action transaction menu and expandable starter category groups.
