"""A no-network judge showing the extension contract, without claiming accuracy."""

from companybench.evidence import packet_hash
from companybench.models import CompanyJudgment, Cost, CriterionJudgment


class ExampleJudge:
    name = "example-judge"
    model = "offline-example-v1"
    api_key_env = None

    async def grade(self, packet, context):
        return CompanyJudgment(
            query_id=packet.query.id,
            entity_id=packet.company.id,
            judge=self.name,
            verdict="unknown",
            conditions=[
                CriterionJudgment(
                    criterion_id=condition.id,
                    verdict="unknown",
                    reason="This example performs no evaluation",
                )
                for condition in packet.query.conditions
            ],
            packet_hash=packet_hash(packet),
            cost=Cost(public_usd=0, basis="No API call"),
        )


def create(options=None):
    return ExampleJudge()
