{#
  Fails for every combination of `columns` that appears on more than one row:
  the grain of an aggregate, stated as a test.
#}
{% test unique_combination(model, columns) %}

select
    {{ columns | join(', ') }},
    count(*) as rows_with_this_key
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1

{% endtest %}
