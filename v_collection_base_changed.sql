CREATE OR REPLACE VIEW auto_views.v_collection_base AS
WITH loans AS (
    SELECT
        mlsi.loan_id,
        mlsi.consumer_id,
        mlsi.loan_schedule_id,
        mlsi.due_principal AS principal_amount,
        mlsi.due_interest AS interest_amount,
        mlsi.late_fee,
        mlsi.due_principal + mlsi.due_interest AS installment_amount,
        mli.loan_booked_date,
        mlsi.due_date,
        mlsi.fin_due_date,
        mlsi.paid_date,
        CASE
            WHEN mlsi.paid_date IS NOT NULL
                THEN mlsi.due_principal + mlsi.due_interest
            ELSE 0::numeric
        END AS paid_amount,
        mlsi.paid_late_payment_fee AS paid_late_fee,
        mli.new_tenor AS tenor,
        mli.merchant_name,
        mli.transacted_merchant_branch_governorate,
        mli.merchant_region,
        mli.merchant_type,
        mli.retail_category,
        mli.checkout_channel,
        mli.loan_batch_status,
        mli.is_postponed::text AS is_postponed,
        mli.is_rescheduled::text AS is_rescheduled,
        mli.loan_rescheduled_at
    FROM mv_loans_sch_info AS mlsi
    LEFT JOIN mv_loans_info AS mli
        ON mlsi.loan_id::text = mli.loan_id::text
)
SELECT
    loans.loan_id,
    loans.consumer_id,
    loans.loan_schedule_id,
    row_number() OVER (
        PARTITION BY loans.loan_id
        ORDER BY loans.due_date
    ) AS installment_number,
    loans.principal_amount,
    loans.interest_amount,
    loans.late_fee,
    loans.installment_amount,
    loans.loan_booked_date,
    loans.due_date,
    loans.fin_due_date,
    loans.paid_date,
    CASE
        WHEN (
            loans.paid_date > loans.due_date
            OR loans.paid_date IS NULL
        )
        AND loans.due_date < CURRENT_DATE
            THEN 1
        ELSE 0
    END AS overdue_flg,
    CASE
        WHEN loans.paid_date IS NOT NULL THEN 1
        WHEN loans.paid_date IS NULL
             AND loans.due_date < CURRENT_DATE THEN 0
        ELSE NULL::integer
    END AS paid_flg,
    CASE
        WHEN loans.paid_date IS NULL THEN NULL::integer
        WHEN loans.paid_date <= loans.due_date THEN 0
        ELSE loans.paid_date - loans.due_date
    END AS days_paid_after_due,
    CASE
        WHEN (
            loans.paid_date > loans.due_date
            OR loans.paid_date IS NULL
        )
        AND loans.due_date < CURRENT_DATE
            THEN COALESCE(loans.paid_date, CURRENT_DATE)
                 - loans.due_date
        ELSE NULL::integer
    END AS delinquency,
    CASE
        WHEN loans.paid_date IS NOT NULL
            THEN loans.installment_amount
        ELSE 0::numeric
    END AS paid_amount,
    loans.paid_late_fee,
    loans.tenor,
    loans.merchant_name,
    loans.transacted_merchant_branch_governorate,
    loans.merchant_region,
    loans.merchant_type,
    loans.retail_category,
    loans.checkout_channel,
    loans.loan_batch_status,
    loans.is_postponed,
    loans.is_rescheduled,
    loans.loan_rescheduled_at,
    mci.national_id,
    mci.consumer_gender,
    mci.consumer_age_range,
    mci.job_sector,
    mci.job_category,
    mci.job_name,
    mci.company,
    mci.governorate_home,
    mci.utilization_bucket,
    mci.region,
    mci.first_activated_at,
    mci.origination_channel,
    mci.activation_branch_adjusted,
    CASE
        WHEN mci.activation_branch_adjusted = 'digital-onboarding'::text
            THEN 'digital-onboarding'::text
        ELSE 'physical'::text
    END AS activation_type,
    mci.status AS consumer_status,
    mci.i_score,
    mci.classification,
    CASE
        WHEN collection_swat_portfolio.consumer_id IS NOT NULL THEN 1
        ELSE 0
    END AS collection_swat_portfolio_flg,

    -- Added migration test columns
    NULL::text AS migration_test,
    NULL::text AS migration_test2,
    NULL::text AS migration_test3

FROM loans
LEFT JOIN mv_consumers_info AS mci
    ON loans.consumer_id::text = mci.consumer_id::text
LEFT JOIN collection_swat_portfolio
    ON loans.consumer_id::text =
       collection_swat_portfolio.consumer_id::text;