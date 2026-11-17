{# The plain meaning of the words typed in a note (invoice reference or payment memo), or null. #}
{% macro note_meaning(col) -%}
nullif(concat_ws(' ',
    case when {{ col }} ~* 'deposit' then 'A refundable deposit is owed back to the customer: it is not revenue, and it inflates billed, open and late figures.' end,
    case when {{ col }} ~* '(pro-?forma|not a request for payment|quotation|\mquote\M|estimate)' then 'A pro-forma or quote is not a real bill: it should not be counted as billed, owed or late.' end,
    case when {{ col }} ~* '(write.?off|bad debt|chapter \d|bankrupt|insolv|uncollect)' then 'A bad-debt write-off, not a customer credit: the invoices it closes read as settled though nothing was collected.' end,
    case when {{ col }} ~* '(\mfees?\M|interest|penalt|surcharge)' then 'A fee billed by hand (late fee or similar): while unpaid it inflates open and late balances, and it is not sales.' end,
    case when {{ col }} ~* '(payment plan|instal+ment|agreement)' then 'Paid under a payment plan: lateness against the original due dates may be agreed, and payments follow the plan.' end,
    case when {{ col }} ~* '(dup|twice|copy|double)' then 'It says something was billed or charged twice.' end,
    case when {{ col }} ~* '(disput|withh[oe]ld|complain|broke|damage|not received|faulty)' and ({{ col }} ~* 'deposit' is not true or {{ col }} ~* '(disput|withh[oe]ld)') then 'A dispute: the amount withheld may not be collectible as billed; confirm before chasing.' end,
    case when {{ col }} ~* '(write.?off|bad debt|deposit)' is not true and {{ col }} ~* '(credit|refund|revers)' then 'A credit, refund or reversal.' end
), '')
{%- endmacro %}
