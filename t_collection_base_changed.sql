-- auto_views.t_collection_base definition

-- Drop table

-- DROP TABLE auto_views.t_collection_base;

CREATE TABLE auto_views.t_collection_base (

    loan_id varchar NULL,
    consumer_id varchar NULL,
    loan_schedule_id int4 NULL,
    installment_number int8 NULL,
    principal_amount numeric NULL,
    interest_amount numeric NULL,
    late_fee numeric NULL,
    installment_amount numeric NULL,
    loan_booked_date date NULL,
    due_date date NULL,
    fin_due_date date NULL,
    paid_date date NULL,
    overdue_flg int4 NULL,
    paid_flg int4 NULL,
    days_paid_after_due int4 NULL,
    delinquency int4 NULL,
    paid_amount numeric NULL,
    paid_late_fee numeric NULL,
    tenor int8 NULL,
    merchant_name varchar(255) NULL,
    transacted_merchant_branch_governorate text NULL,
    merchant_region text NULL,
    merchant_type text NULL,
    retail_category varchar(200) NULL,
    checkout_channel varchar NULL,
    loan_batch_status text NULL,
    is_postponed text NULL,
    is_rescheduled text NULL,
    loan_rescheduled_at timestamp NULL,
    national_id varchar NULL,
    consumer_gender text NULL,
    consumer_age_range text NULL,
    job_sector text NULL,
    job_category text NULL,
    job_name text NULL,
    company varchar NULL,
    governorate_home text NULL,
    utilization_bucket text NULL,
    region varchar NULL,
    first_activated_at timestamp NULL,
    origination_channel varchar NULL,
    activation_branch_adjusted text NULL,
    activation_type text NULL,
    consumer_status text NULL,
    i_score int4 NULL,
    classification varchar NULL,
    collection_swat_portfolio_flg int4 NULL,
    test11 varchar

);
