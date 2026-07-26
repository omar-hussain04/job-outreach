subject: "Re: {{ role_target }} — {{ me.full_name_en }}, {{ me.major_en }}"
---
Hi {{ contact_name or "there" }},

I wrote to you on {{ initial_sent_date }} about {{ me.seeking_short_en }} at {{ company_name }}. I know inboxes get busy, so I wanted to bring this back to the top of yours.

The short version: I'm {{ me.headline_en }}. {{ me.availability_en }}. My CV is attached again below.

If there's nothing open right now, just say the word and I won't follow up again — but I'd still appreciate knowing when the next intake opens.

Thanks again,

{{ me.full_name_en }}
{{ me.title_en }}
{{ me.email }}{% if me.phone %} · {{ me.phone }}{% endif %}{% if me.phone_alt %} · {{ me.phone_alt }}{% endif %}
{%- if me.linkedin %}
LinkedIn: {{ me.linkedin }}
{%- endif %}
