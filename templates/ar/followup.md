subject: "Re: {{ role_target }} — {{ me.full_name_ar }}، {{ me.major_ar }}"
---
{% if contact_name %}الأستاذ/ة {{ contact_name }}، تحية طيبة{% else %}السلام عليكم ورحمة الله وبركاته{% endif %}

كنت قد راسلتكم بتاريخ {{ initial_sent_date }} بخصوص {{ me.seeking_short_ar }} لدى {{ company_name }}، وأعلم أن البريد يزدحم، لذلك أعدت الرسالة إلى أعلى صندوقكم.

باختصار: أنا {{ me.headline_ar }}. {{ me.availability_ar }}. وسيرتي الذاتية مرفقة مجدداً أدناه.

وإن لم تكن هناك فرصة متاحة حالياً، يكفي أن تخبروني ولن أعاود المراسلة — لكن سيسعدني معرفة موعد فتح باب التقديم القادم.

شاكراً لكم مجدداً،

{{ me.full_name_ar }}
{{ me.title_ar }}
{{ me.email }}{% if me.phone %} · {{ me.phone }}{% endif %}{% if me.phone_alt %} · {{ me.phone_alt }}{% endif %}
{%- if me.linkedin %}
لينكدإن: {{ me.linkedin }}
{%- endif %}
