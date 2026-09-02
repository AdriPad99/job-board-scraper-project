from typing import Literal, Optional
from pydantic import BaseModel, Field

class Job(BaseModel):
    
    job_url: str = Field(description="URL of the job posting")
    
class JobDetails(BaseModel):

    job_title: str = Field(description="Title of the job posting")
    description: str = Field(description="Description of the job posting")
    salary: Optional[int] = Field(description="Salary of the job posting")
    location: Optional[str] = Field(description="The job's stated location, e.g. 'Austin, TX', 'New York, NY', or 'Remote'. Null if the posting doesn't state one.")
    workplace_type: Optional[Literal['REMOTE', 'ON_SITE', 'HYBRID']] = Field(description="Whether the role is fully REMOTE, ON_SITE (in person at a location), or HYBRID, per the posting. Null if it can't be determined.")
    
class JobList(BaseModel):
    
    jobs: list[Job] = Field(description="Jobs obtained from the page scrape")
    
class AppliableJob(BaseModel):

    reasoning: str = Field(description="Brief explanation of the recommendation covering experience match, tech stack overlap, seniority fit, degree requirement, and transferable relevance.")
    recommendation: Literal['APPLY', 'STRETCH', 'SKIP'] = Field(description="""
        - APPLY: Strong overlap, junior-friendly, realistic requirements
        - STRETCH: Some gaps but worth trying (e.g. bachelor's preferred not required, or 2-3 yr exp req)
        - SKIP: Major mismatches (senior-level, wrong stack, 5+ years required, requires degree candidate doesn't have)
                                                                """)
    # user_should_apply_to_job: bool = Field(description="Boolean to determine whether or not the user should apply to this job.")
    # job_url: str = Field(description="URL of the webpage the job is hosted on")
    # apply_reason: str = Field(description="Reason as to why the job selected should be applied to.")
    
class Prettyizer(BaseModel):

    formatted_content: str = Field(description="Easy to read markdown conversion of input contents. The markdown file should also contain the links to the job postings.")


class ScreeningAnswer(BaseModel):

    question: str = Field(description="A screening/application question the candidate is likely to be asked for this specific role (e.g. work authorization, why this company, relevant experience, salary expectations).")
    answer: str = Field(description="A truthful, ready-to-paste answer drafted only from the candidate's actual resume — never fabricated.")


class ApplicationDraft(BaseModel):

    company_name: str = Field(description="The hiring company's name exactly as stated in the posting (e.g. 'WHOOP'). Empty string if it can't be determined.")
    fit_summary: str = Field(description="Honest 2-3 sentence read on how the candidate fits this role, explicitly naming any gaps.")
    cover_letter: str = Field(description="A tailored, ready-to-send cover letter for THIS posting, grounded only in the candidate's real resume experience and written in their voice.")
    resume_suggestions: list[str] = Field(description="Concrete tweaks to the candidate's resume bullets to better target this posting — rephrasings/emphasis of real experience only, never invented content.")
    screening_answers: list[ScreeningAnswer] = Field(description="Drafted answers to the questions this application most likely asks.")


class TailoredResume(BaseModel):

    company_name: str = Field(description="The hiring company's name exactly as stated in the posting (e.g. 'WHOOP'). Empty string if it can't be determined.")
    latex_source: str = Field(description="The complete, compilable LaTeX source of the tailored resume — the FULL document, using the same document class, packages, and custom macros as the input. No fabricated content.")
    change_summary: list[str] = Field(description="Concrete bullets describing what was changed and why, tied to the job posting.")


class LatexFix(BaseModel):

    latex_source: str = Field(description="The corrected, complete LaTeX source that resolves the compiler error, with the resume's wording/content left unchanged.")


class EmailTriageItem(BaseModel):

    index: int = Field(description="The 0-based index of this email in the provided list, copied exactly.")
    category: Literal['CONFIRMATION', 'REJECTION', 'OFFER', 'INTERVIEW', 'NOT_JOB'] = Field(description="""
        - CONFIRMATION: acknowledgement that the candidate's own application was received/submitted.
        - REJECTION: notice the candidate was not selected or is no longer being considered.
        - OFFER: a job offer, or advancement to the offer/decision stage, for the candidate.
        - INTERVIEW: an invitation to interview, schedule a call, or a recruiter moving the candidate forward.
        - NOT_JOB: anything else — newsletters, marketing, receipts, personal mail, and (importantly) job-board
          alert/digest emails listing NEW jobs to apply to (those are not about the candidate's own applications).
        """)
    company: Optional[str] = Field(description="Hiring company / employer name if identifiable from the email, else null.")
    role: Optional[str] = Field(description="Job title / role the email references, if identifiable, else null.")
    summary: str = Field(description="One concise plain-English sentence summarizing what the email says.")


class EmailTriage(BaseModel):

    items: list[EmailTriageItem] = Field(description="Exactly one classification per input email, preserving each email's index.")