"""A Python query factory with explicit, individually judged conditions."""

from companybench.models import Criterion, Query, Rule


def create_queries():
    return [
        Query(
            id="EXAMPLE-001",
            index=1,
            query="Companies headquartered in Canada that manufacture industrial pumps",
            family="firmographic",
            complexity="L2",
            industry="Manufacturing",
            geography="Canada",
            geography_basis="headquarters",
            acceptance="Headquarters is in Canada AND the company manufactures industrial pumps",
            conditions=[
                Criterion(
                    id="headquarters", description="Current company headquarters is in Canada"
                ),
                Criterion(
                    id="manufacturer",
                    description="The company manufactures industrial pumps, rather than only reselling them",
                ),
            ],
            rule=Rule(
                op="all",
                children=[
                    Rule(op="condition", condition_id="headquarters"),
                    Rule(op="condition", condition_id="manufacturer"),
                ],
            ),
            company_unit="Operating business; independently operated subsidiaries may qualify",
            evidence="Official company headquarters and product/manufacturing pages",
            gtm_use_case="Build an account list for manufacturing software",
            source_basis="original",
        )
    ]
