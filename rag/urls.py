from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/documents", views.documents, name="documents"),
    path("api/documents/<int:pk>/delete", views.delete_document, name="delete_document"),
    path("api/ask", views.ask, name="ask"),
    path("healthz", views.healthz, name="healthz"),
]
