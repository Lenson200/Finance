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

The web client caches its static application shell and the authenticated
transaction-entry page, stores offline entries in IndexedDB, and uses Background
Sync where available (with an online-event/manual-sync fallback elsewhere).
Only static assets and transaction entry are cached; transaction history and
other API responses are never cached. Open transaction entry once while online
before relying on it offline. Offline entries keep their account, date, and
exchange-rate snapshot; foreign-currency entries without a saved quote cannot
be queued. The server validates every entry when syncing.

Each queued transaction carries a UUID idempotency key. Retrying the same
payload returns the existing posting rather than creating another ledger entry.
If the key conflicts with different transaction data or the server rejects an
entry, it remains in a visible review state instead of being silently dropped.
Offline queue data and the cached transaction-entry page are stored on the
device; sign-out clears the cached page, while unsynced entries remain associated
with their original user and are not submitted for another signed-in user.

## Timezones and transaction dates

The browser's IANA timezone is detected and saved per user. Django activates that
timezone for local UI rendering and transaction date entry, while date/time
values are stored as timezone-aware instants. A shared workspace's configured
timezone remains the reference for shared reporting, so one member traveling
does not shift another member's report dates.

Completed income, expense, and transfer transactions cannot be dated in the
future. Future dates are reserved for planning features such as budgets, bill
due dates, savings targets, scheduled payments, and investment maturity.
Features marked Planned are policy guidance only and are not yet recordable.

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

First-party Django applications live under `apps/` (`users`, `workspaces`,
`accounts`, `categories`, `transactions`, and `dashboard`). Shared browser
behavior is kept in `apps/dashboard/static/js/main.js`; templates provide
structure and data attributes rather than inline JavaScript.

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

Account list entries open an account detail page showing its opening balance,
current balance, reporting-currency equivalent, and transaction history. The
history can be searched and filtered by type and date range, and exported as
CSV. The account list can also be searched and filtered by type and currency.
Transaction History is read-only; recording is on a separate page reached from
the add button or the Record transaction action. Recent transaction suggestions
and category options are limited to the currently selected transaction type.

Shared workspaces use account-level access grants: workspace membership alone
does not reveal an account or its transactions. The account creator and
workspace owners receive access by default; other members need an explicit
view or transaction-writing grant from Account management in Settings.
Transaction history, account lists, exports, reports, and balances use those
grants. A transfer is visible from a shared side while an unshared account's
identity remains private; posting a transfer requires write access to both
accounts.

The Overview emphasizes available balance (bank, cash, and mobile-money
balances less short-term debt), with long-term debt shown separately. Savings
are not included in available balance. Income, spending, and cashflow
charts can be filtered by preset or custom date ranges, with recent transactions
below. Home remains a separate dashboard with the classic Net worth, Income
this month, and Expenses this month summary cards. Bills & payments is a
planned-feature placeholder for recurring payments, future payments, and
budgets; it does not yet schedule or record them.

## Savings goals and financial analytics

Users can create workspace-scoped savings goals with a name, category, target
amount, current savings, target date, and daily, weekly, or monthly contribution
pace. Goal cards include local illustrations, progress, the remaining amount,
and a suggested contribution based on the time left. Reaching 75% or completing
a goal produces an in-app message and progress celebration. Goals are private
to their creator and track manually entered savings; they do not move funds or
change account balances.

Expense entries can be classified as planned, unplanned, emergency,
unavoidable, or unsure. Reports provide week, month, year, and custom date
ranges, an expense-category filter, currency-converted income and expense
summaries, net savings, six-month cashflow, goal progress, intent totals, and
smart insights. The total-balance card compares accessible-account balances
with the previous period; selecting an expense category does not change income
or total balance. Refunds reduce their original expense category, and
refund-only categories do not create negative chart widths.

Reports can be downloaded as CSV in summary, expense, income, savings/goals,
and transaction formats. PDF and XLSX downloads are not currently available.
