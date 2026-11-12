from engine.risk_score import assess_transaction


def main():
    sample_transaction = {
        "transaction_id": "TXN1024",
        "timestamp": "2026-08-10 02:15:00",
        "amount": 85000,
        "merchant": "Unknown Merchant",
        "location": "Delhi",
        "previous_location": "Mumbai",
        "payment_method": "UPI",
        "device_id": "DEV98",
        "transactions_last_10min": 5,
        "failed_attempts": 2,
        "is_new_merchant": True,
    }

    result = assess_transaction(sample_transaction)
    print(result)


if __name__ == "__main__":
    main()
