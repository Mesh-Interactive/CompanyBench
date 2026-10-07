"""A runnable provider extension; its results are invented offline fixtures."""

from companybench.models import CompanyCandidate, Cost, SearchResult
from companybench.providers.base import ImmediateProvider


class ExampleProvider(ImmediateProvider):
    name = "offline-example"
    readiness = "synthetic example"

    async def search(self, request, context):
        return SearchResult(
            provider=self.name,
            candidates=[
                CompanyCandidate(
                    position=1, name="Invented Example Company", domain="company.example"
                )
            ],
            cost=Cost(public_usd=0, basis="No API call; synthetic fixture"),
            metadata={"synthetic": True},
        )


def create(**options):
    return ExampleProvider()
