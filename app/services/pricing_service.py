from __future__ import annotations

from app.extensions import db
from app.models import ToolCatalog, User, WalletTransaction


class PricingService:
    @staticmethod
    def debit_wallet(
        user: User,
        amount_paise: int,
        reference: str,
        note: str,
        transaction_type: str = "debit",
    ) -> WalletTransaction:
        if amount_paise <= 0:
            raise ValueError("Debit amount must be positive.")
        if user.wallet_balance_paise < amount_paise:
            raise ValueError("Insufficient wallet balance.")
        user.wallet_balance_paise -= amount_paise
        transaction = WalletTransaction(
            user_id=user.id,
            transaction_type=transaction_type,
            amount_paise=-amount_paise,
            balance_after_paise=user.wallet_balance_paise,
            reference=reference,
            note=note,
        )
        db.session.add(transaction)
        db.session.commit()
        return transaction

    @staticmethod
    def top_up_wallet(
        user: User,
        amount_paise: int,
        reference: str = "MOCK-TOPUP",
        note: str = "Mock payment gateway top-up",
    ) -> WalletTransaction:
        if amount_paise <= 0:
            raise ValueError("Top-up amount must be positive.")
        user.wallet_balance_paise += amount_paise
        transaction = WalletTransaction(
            user_id=user.id,
            transaction_type="topup",
            amount_paise=amount_paise,
            balance_after_paise=user.wallet_balance_paise,
            reference=reference,
            note=note,
        )
        db.session.add(transaction)
        db.session.commit()
        return transaction

    @staticmethod
    def charge_tool(user: User, tool: ToolCatalog, reference: str) -> WalletTransaction | None:
        amount = tool.price_paise if tool.is_payperuse_allowed else 0
        if amount <= 0:
            return None
        return PricingService.debit_wallet(
            user=user,
            amount_paise=amount,
            reference=reference,
            note=f"Tool charge for {tool.name}",
            transaction_type="debit",
        )

    @staticmethod
    def refund(user: User, amount_paise: int, reference: str, note: str) -> WalletTransaction | None:
        if amount_paise <= 0:
            return None
        user.wallet_balance_paise += amount_paise
        transaction = WalletTransaction(
            user_id=user.id,
            transaction_type="refund",
            amount_paise=amount_paise,
            balance_after_paise=user.wallet_balance_paise,
            reference=reference,
            note=note,
        )
        db.session.add(transaction)
        db.session.commit()
        return transaction
