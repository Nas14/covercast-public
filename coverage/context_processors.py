"""Values every page needs.

NFR-1 (transparency): while any person in the database is simulated, every
page says so, so a screenshot of the demo can never pass for real data.
"""

from workforce.codes import DataOrigin
from workforce.models import Employee


def demo_data(request):
    return {"simulated_data": Employee.objects.filter(data_origin=DataOrigin.SIMULATED).exists()}
