# SocietyGuard Phase 2: Visitor Registration

This version adds a database and a visitor registration UI.

## Setup

1.  **Install Requirements**:
    ```bash
    pip install -r requirements.txt
    ```

## How to Run

1.  **Start the Server**:
    ```bash
    uvicorn societyguard_phase2:app --host 0.0.0.0 --port 8000
    ```

2.  **Use the App**:
    -   Scan the original QR code (`flat101_qr.png`).
    -   You will be verified and redirected to the **Visitor Registration** page.
    -   Enter visitor details and submit.

## Database
-   Data is stored in `iam_society.db` (SQLite).
-   Table: `pending_visits`.
