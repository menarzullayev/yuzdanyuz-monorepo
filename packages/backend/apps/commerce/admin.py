from django.contrib import admin

from .models import (
    OrganizationSubscription,
    PaymentIntent,
    Referral,
    ReferralCode,
    SubscriptionPlan,
    Wallet,
    WalletTransaction,
)


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance_coins', 'pending_cash_uzs', 'updated_at')
    raw_id_fields = ('user',)
    readonly_fields = ('balance_coins', 'pending_cash_uzs', 'created_at', 'updated_at')


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'kind', 'coins_delta', 'cash_uzs_delta', 'created_at')
    list_filter = ('kind',)
    raw_id_fields = ('wallet', 'payment_intent')
    readonly_fields = (
        'wallet',
        'kind',
        'coins_delta',
        'cash_uzs_delta',
        'balance_after_coins',
        'balance_after_cash_uzs',
        'description',
        'metadata',
        'payment_intent',
        'created_at',
    )


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = ('user', 'provider', 'amount_uzs', 'coins_to_credit', 'status', 'created_at')
    list_filter = ('provider', 'status')
    raw_id_fields = ('user', 'target_subscription_plan', 'target_organization')
    readonly_fields = ('provider_tx_id', 'created_at', 'updated_at')


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'billing_period', 'pricing_model', 'price_uzs', 'is_active')
    list_filter = ('billing_period', 'pricing_model', 'is_active')
    search_fields = ('name', 'slug')


@admin.register(OrganizationSubscription)
class OrganizationSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('organization', 'plan', 'status', 'current_period_ends_at', 'auto_renew')
    list_filter = ('status', 'auto_renew')
    raw_id_fields = ('organization', 'plan', 'last_payment_intent')
    readonly_fields = ('started_at', 'created_at', 'updated_at')


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ('user', 'code', 'is_withdrawable', 'created_at')
    raw_id_fields = ('user',)
    readonly_fields = ('code', 'created_at')


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ('inviter', 'invited', 'coin_reward', 'created_at')
    raw_id_fields = ('inviter', 'invited')
    readonly_fields = ('created_at',)
