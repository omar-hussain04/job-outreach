subject: "{{ role_target }} — {{ me.full_name_ar }}، {{ me.major_ar }}"
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

{{ me.availability_ar }}. أرفقت سيرتي الذاتية{% if attachment_names|length > 1 %} مع {{ attachment_names[1:]|join("، ") }}{% endif %} لمزيد من التفاصيل.

هل يمكن ترتيب مكالمة قصيرة خلال هذا الشهر؟ وإن لم يكن التوظيف من اختصاصكم، سأكون ممتناً لو دللتموني على الشخص المناسب.

شاكراً لكم حسن تعاونكم،

{{ me.full_name_ar }}
{{ me.title_ar }} · {{ me.major_ar }} — {{ me.university_ar }}
{{ me.email }}{% if me.phone %} · {{ me.phone }}{% endif %}{% if me.phone_alt %} · {{ me.phone_alt }}{% endif %}
{%- if me.linkedin %}
لينكدإن: {{ me.linkedin }}
{%- endif %}
{%- if me.github %}
جيت‌هَب: {{ me.github }}
{%- endif %}
{%- if me.portfolio %}
معرض الأعمال: {{ me.portfolio }}
{%- endif %}
