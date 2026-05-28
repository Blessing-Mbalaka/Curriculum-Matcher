from django import forms
from .models import Course, Module


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["code", "name", "description"]
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "e.g. HRD101"}),
            "name": forms.TextInput(attrs={"placeholder": "e.g. Human Resource Development"}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Brief overview of the course"}),
        }


class ModuleForm(forms.ModelForm):
    class Meta:
        model = Module
        fields = ["name", "content", "order"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Introduction to Excel"}),
            "content": forms.Textarea(attrs={
                "rows": 10,
                "placeholder": "Paste the full module content, syllabus, or learning outcomes here.\n\nThe more detail you provide, the better the skill matching will be.\n\nExample:\n- Microsoft Excel: formulas, pivot tables, VLOOKUP\n- Data cleaning and validation\n- Chart creation and formatting"
            }),
            "order": forms.NumberInput(attrs={"placeholder": "1"}),
        }
