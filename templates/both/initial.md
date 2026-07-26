subject: "{{ role_target }} — {{ me.full_name_ar }} | {{ me.full_name_en }}"
---
{% if contact_name %}الأستاذ/ة {{ contact_name }}، تحية طيبة{% else %}السلام عليكم ورحمة الله وبركاته{% endif %}

أنا {{ me.full_name_ar }}، {{ me.headline_ar }}. أكتب إليكم للاستفسار عمّا إذا كان لديكم في {{ company_name }} {{ me.seeking_ar }}.
{% if custom_note %}
{{ custom_note }}
{% endif %}
ما أستطيع تقديمه لفريقكم:
{%- for skill in me.skills_ar %}
- {{ skill }}
{%- endfor %}

{{ me.achievement_ar }}

{{ me.availability_ar }}. أرفقت سيرتي الذاتية لمزيد من التفاصيل، وتجدون النسخة الإنجليزية من هذه الرسالة أدناه.

هل يمكن ترتيب مكالمة قصيرة خلال هذا الشهر؟ وإن لم يكن التوظيف من اختصاصكم، سأكون ممتناً لو دللتموني على الشخص المناسب.

—— English version ——

Hi {{ contact_name or "there" }},

My name is {{ me.full_name_en }} — {{ me.headline_en }}. I'm reaching out to ask whether {{ company_name }} has an opening for {{ me.seeking_en }}.

What I'd bring to the team:
{%- for skill in me.skills_en %}
- {{ skill }}
{%- endfor %}

{{ me.achievement_en }}

{{ me.availability_en }}. My CV is attached.

Would you be open to a short call this month? And if hiring isn't handled by you, I'd be grateful if you could point me to the right person.

شاكراً لكم حسن تعاونكم — Thank you for your time,

{{ me.full_name_ar }} | {{ me.full_name_en }}
{{ me.title_ar }} · {{ me.title_en }} — {{ me.university_en }}
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
