subject: "Re: {{ role_target }} — {{ me.full_name_ar }} | {{ me.full_name_en }}"
---
{% if contact_name %}الأستاذ/ة {{ contact_name }}، تحية طيبة{% else %}السلام عليكم ورحمة الله وبركاته{% endif %}

كنت قد راسلتكم بتاريخ {{ initial_sent_date }} بخصوص {{ me.seeking_short_ar }} لدى {{ company_name }}، وأعلم أن البريد يزدحم، لذلك أعدت الرسالة إلى أعلى صندوقكم. سيرتي الذاتية مرفقة مجدداً، والنسخة الإنجليزية أدناه.

وإن لم تكن هناك فرصة متاحة حالياً، يكفي أن تخبروني ولن أعاود المراسلة — لكن سيسعدني معرفة موعد فتح باب التقديم القادم.

—— English version ——

Hi {{ contact_name or "there" }},

I wrote to you on {{ initial_sent_date }} about {{ me.seeking_short_en }} at {{ company_name }}. I know inboxes get busy, so I wanted to bring this back to the top of yours. My CV is attached again.

If there's nothing open right now, just say the word and I won't follow up again — but I'd still appreciate knowing when the next intake opens.

شاكراً لكم مجدداً — Thanks again,

{{ me.full_name_ar }} | {{ me.full_name_en }}
{{ me.title_ar }} · {{ me.title_en }}
{{ me.email }}{% if me.phone %} · {{ me.phone }}{% endif %}{% if me.phone_alt %} · {{ me.phone_alt }}{% endif %}
{%- if me.linkedin %}
LinkedIn: {{ me.linkedin }}
{%- endif %}
