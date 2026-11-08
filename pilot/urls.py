from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("inbox/", views.inbox, name="inbox"),
    path("inbox/<int:pk>/", views.message_detail, name="message"),
    path("inbox/<int:pk>/decide/", views.decide, name="decide"),
    path("simulate/", views.simulate, name="simulate"),
    path("api/inbound/", views.inbound, name="inbound"),
]
