subject: "{{ role_target }} — {{ me.full_name_en }}, {{ me.major_en }}"
---
Hi {{ contact_name or "there" }},

My name is {{ me.full_name_en }} — {{ me.headline_en }}. I'm reaching out to ask whether {{ company_name }} has an opening for {{ me.seeking_en }}.
{% if custom_note %}
{{ custom_note }}
{% endif %}
What I'd bring to the team:
{%- for skill in me.skills_en %}
- {{ skill }}
{%- endfor %}

{{ me.achievement_en }}

{{ me.availability_en }}. I've attached my CV{% if attachment_names|length > 1 %} along with {{ attachment_names[1:]|join(", ") }}{% endif %} for the details.

Would you be open to a short call this month? And if hiring isn't handled by you, I'd be grateful if you could point me to the right person.

Thank you for your time,

{{ me.full_name_en }}
{{ me.title_en }} · {{ me.major_en }}, {{ me.university_en }}
{{ me.email }}{% if me.phone %} · {{ me.phone }}{% endif %}{% if me.phone_alt %} · {{ me.phone_alt }}{% endif %}
{%- if me.linkedin %}
LinkedIn: {{ me.linkedin }}
{%- endif %}
{%- if me.github %}
GitHub: {{ me.github }}
{%- endif %}
{%- if me.portfolio %}
Portfolio: {{ me.portfolio }}
{%- endif %}
