"""Seeded FAQ corpus for the Tax Help Assistant.

Built against docs/spec/02_TECHNICAL_DESIGN.md §6, as committed at
eb360db. 18 short documents covering common tax questions -- within the
10-20 range the spec calls for. Deliberately topically distinct (each
document covers one clear subject) so hybrid retrieval has a genuine,
checkable "right answer" per query, which is what
test_unit_retrieval.py's known-expected-top-result tests rely on.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FAQDocument:
    """A single FAQ entry.

    Attributes:
        doc_id: Stable identifier, used as the retrieval unit and as
            the "sources" value returned to a user (FR-7, HelpAskResponse
            in 03_API_CONTRACT.yaml).
        title: Short question-style title.
        content: The answer -- 2-4 sentences, plain language.
    """

    doc_id: str
    title: str
    content: str


FAQ_CORPUS = [
    FAQDocument(
        doc_id="faq-1040x",
        title="What is Form 1040-X?",
        content=(
            "Form 1040-X is used to amend a tax return you've already filed, "
            "for example to correct your income, filing status, or credits. "
            "You generally have three years from the original filing date to "
            "submit an amendment. Amended returns are processed separately "
            "from original returns and are not tracked by this refund status "
            "tool."
        ),
    ),
    FAQDocument(
        doc_id="faq-identity-verification",
        title="Why am I being asked to verify my identity?",
        content=(
            "The IRS sometimes flags a return for identity verification to "
            "protect against tax-related identity theft. This can happen even "
            "if nothing is wrong with your return. You'll usually get a "
            "letter (5071C or similar) with instructions -- verifying "
            "promptly is the fastest way to keep your refund moving."
        ),
    ),
    FAQDocument(
        doc_id="faq-still-processing",
        title="What does \"still processing\" mean?",
        content=(
            "\"Still processing\" means the IRS has your return but hasn't "
            "finished reviewing it yet, and no specific delay reason is known. "
            "This is normal and doesn't mean something is wrong -- most "
            "returns move through this stage within a few weeks."
        ),
    ),
    FAQDocument(
        doc_id="faq-path-act",
        title="What is the PATH Act and why does it delay my refund?",
        content=(
            "The PATH Act is a federal law that requires the IRS to hold "
            "refunds for returns claiming the Earned Income Tax Credit or "
            "Additional Child Tax Credit until at least mid-February, even if "
            "you filed in January. This is a standard, expected hold, not a "
            "sign of a problem with your return."
        ),
    ),
    FAQDocument(
        doc_id="faq-refund-timing",
        title="How long does it take to get my refund after e-filing?",
        content=(
            "Most e-filed returns with direct deposit are processed within "
            "21 days. Paper returns and returns needing manual review can "
            "take significantly longer, sometimes several weeks to a couple "
            "of months."
        ),
    ),
    FAQDocument(
        doc_id="faq-treasury-offset",
        title="Why is my refund less than I expected?",
        content=(
            "The Treasury Offset Program can reduce or redirect your refund "
            "to cover certain past-due debts, such as unpaid federal or state "
            "taxes, child support, or defaulted student loans. If this "
            "happens, you'll receive a separate notice explaining the offset "
            "amount and the agency it was sent to."
        ),
    ),
    FAQDocument(
        doc_id="faq-status-meanings",
        title="What's the difference between Received, Approved, and Sent?",
        content=(
            "\"Received\" means the IRS has your return but hasn't finished "
            "reviewing it. \"Approved\" means your refund amount is confirmed "
            "and a send date is being prepared. \"Sent\" means the refund has "
            "actually been issued via direct deposit or mail."
        ),
    ),
    FAQDocument(
        doc_id="faq-change-direct-deposit",
        title="Can I change my direct deposit information after filing?",
        content=(
            "No -- once a return is filed, you cannot change the bank account "
            "information on it. If the account is closed or incorrect, the "
            "deposit will typically be rejected and the IRS will mail a paper "
            "check to your address on file instead."
        ),
    ),
    FAQDocument(
        doc_id="faq-mistake-on-return",
        title="What if I made a mistake on my tax return?",
        content=(
            "Small math errors are often corrected automatically by the IRS "
            "during processing. For anything more substantial -- like "
            "incorrect income or a missed credit -- you'll need to file Form "
            "1040-X to amend the return once the original has finished "
            "processing."
        ),
    ),
    FAQDocument(
        doc_id="faq-cryptocurrency",
        title="Do I need to report cryptocurrency on my taxes?",
        content=(
            "Yes. Selling, trading, or spending cryptocurrency is generally a "
            "taxable event, and many tax returns now include a direct "
            "question about digital asset activity. Keep records of your "
            "transactions, since gains and losses need to be reported."
        ),
    ),
    FAQDocument(
        doc_id="faq-eitc",
        title="What is the Earned Income Tax Credit (EITC)?",
        content=(
            "The EITC is a refundable credit for low-to-moderate income "
            "workers, especially those with children. It can significantly "
            "increase a refund, but returns claiming it are held under the "
            "PATH Act until at least mid-February."
        ),
    ),
    FAQDocument(
        doc_id="faq-ctc",
        title="What is the Child Tax Credit (CTC)?",
        content=(
            "The Child Tax Credit reduces your tax bill for each qualifying "
            "child, and part of it may be refundable as the Additional Child "
            "Tax Credit. Like the EITC, returns claiming it are subject to "
            "the PATH Act's mid-February hold."
        ),
    ),
    FAQDocument(
        doc_id="faq-paper-filing-delay",
        title="Why does paper filing take longer than e-filing?",
        content=(
            "Paper returns must be manually opened, sorted, and entered into "
            "IRS systems before processing can even begin, unlike e-filed "
            "returns which enter electronically. This can mean weeks of "
            "delay before a paper return shows any status at all -- that "
            "absence of status is expected, not an error."
        ),
    ),
    FAQDocument(
        doc_id="faq-missing-w2",
        title="What should I do if I haven't received my W-2?",
        content=(
            "Contact your employer first, since W-2s are legally required to "
            "be sent by January 31. If it still doesn't arrive, you can "
            "contact the IRS for help or file using Form 4852 as a "
            "substitute, estimating your wages and withholding as closely as "
            "possible."
        ),
    ),
    FAQDocument(
        doc_id="faq-check-status-no-account",
        title="Can I check my refund status without creating an account?",
        content=(
            "Yes -- refund status lookup only requires your return "
            "identifier and the tax year, not a separate account "
            "registration. This tool and the IRS's own \"Where's My "
            "Refund\" service both work this way."
        ),
    ),
    FAQDocument(
        doc_id="faq-missed-deadline",
        title="What happens if I miss the tax filing deadline?",
        content=(
            "If you're owed a refund, there's typically no penalty for "
            "filing late, though you should still file as soon as possible. "
            "If you owe tax, penalties and interest can start accruing "
            "immediately after the deadline, so filing an extension in "
            "advance is worth doing if you think you'll miss it."
        ),
    ),
    FAQDocument(
        doc_id="faq-refund-taxable",
        title="Is my refund taxable income next year?",
        content=(
            "A federal refund is not taxable income. If you itemized "
            "deductions and received a *state* refund, part of that state "
            "refund can occasionally be taxable the following year -- but "
            "your federal refund itself never is."
        ),
    ),
    FAQDocument(
        doc_id="faq-tax-transcript",
        title="What is a tax transcript and how do I get one?",
        content=(
            "A tax transcript is a summary of your return or account "
            "information, often required for loan applications or income "
            "verification. You can request one for free directly from the "
            "IRS, either online, by mail, or by phone."
        ),
    ),
]
