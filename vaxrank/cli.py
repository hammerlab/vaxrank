# Copyright (c) 2016. Mount Sinai School of Medicine
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import, print_function, division
import sys
import logging
import logging.config
import pickle
import pkg_resources

from varcode.cli import variant_collection_from_args
from mhctools.cli import (
    add_mhc_args,
    mhc_alleles_from_args,
    mhc_binding_predictor_from_args,
)
from isovar.cli.variant_sequences import make_variant_sequences_arg_parser
from isovar.cli.rna_reads import allele_reads_generator_from_args

from .core_logic import ranked_vaccine_peptides, dataframe_from_ranked_list
from .report import (
    compute_template_data,
    make_ascii_report,
    make_html_report,
    make_pdf_report,
)


logging.config.fileConfig(pkg_resources.resource_filename(__name__, 'logging.conf'))
logger = logging.getLogger(__name__)


# inherit all commandline options from Isovar
arg_parser = make_variant_sequences_arg_parser(
    prog="vaxrank",
    description=(
        "Select personalized vaccine peptides from cancer variants, "
        "expression data, and patient HLA type."),
)

add_mhc_args(arg_parser)

###
# OUTPUT ARGS
###

output_args_group = arg_parser.add_argument_group("Output options")

output_args_group.add_argument(
    "--output-patient-id",
    default="",
    help="Patient ID to use in report")

output_args_group.add_argument(
    "--output-csv",
    default="",
    help="Name of CSV file which contains predicted sequences")

output_args_group.add_argument(
    "--output-ascii-report",
    default="",
    help="Path to ASCII vaccine peptide report")

output_args_group.add_argument(
    "--output-html-report",
    default="",
    help="Path to HTML vaccine peptide report")

output_args_group.add_argument(
    "--output-pdf-report",
    default="",
    help="Path to PDF vaccine peptide report")

output_args_group.add_argument(
    "--output-pickle-file",
    default="",
    help="Path to output pickle file containing report template data")

output_args_group.add_argument(
    "--output-reviewed-by",
    default="",
    help="Comma-separated list of reviewer names")

output_args_group.add_argument(
    "--output-final-review",
    default="",
    help="Name of final reviewer of report")


###
# VACCINE PEPTIDE ARGS
###

vaccine_peptide_group = arg_parser.add_argument_group("Vaccine peptide options")
vaccine_peptide_group.add_argument(
    "--vaccine-peptide-length",
    default=25,
    type=int,
    help="Number of amino acids in the vaccine peptides (default %(default)s)")

vaccine_peptide_group.add_argument(
    "--padding-around-mutation",
    default=0,
    type=int,
    help=(
        "Number of off-center windows around the mutation to consider "
        "as vaccine peptides (default %(default)s)"
    ))

vaccine_peptide_group.add_argument(
    "--max-vaccine-peptides-per-mutation",
    default=1,
    type=int,
    help="Number of vaccine peptides to generate for each mutation")

vaccine_peptide_group.add_argument(
    "--max-mutations-in-report",
    default=10,
    type=int,
    help="Number of mutations to report")

vaccine_peptide_group.add_argument(
    "--min-epitope-score",
    default=0.0001,
    type=float,
    help="Ignore epitopes whose normalized score falls below this threshold")

###
# MISC. ARGS
###

arg_parser.add_argument(
    "--input-pickle-file",
    default="",
    help="Path to input pickle file containing report template data. "
    "If present, other report-relevant options will be ignored.")


def load_template_data(args):
    if args.input_pickle_file:
        f = open(args.input_pickle_file, 'rb')
        template_data = pickle.load(f)
        f.close()
        logger.info('Loaded pickled template data from %s', args.input_pickle_file)
        # set the template data input_pickle_file arg; all other args remain unchanged
        # since we want the displayed args to reflect what was previously pickled
        arg_list = template_data['args']
        index = [i for i, x in enumerate(arg_list) if x[0] == 'input_pickle_file'][0]
        arg_list[index] = ('input_pickle_file', args.input_pickle_file)
        return template_data

    interesting_args = []
    for key, value in sorted(vars(args).items()):
        if not key.startswith('output'):
            interesting_args.append((key, value))

    if len(args.output_patient_id) == 0:
        logger.warn("Please specify --output-patient-id; defaulting to unknown")
        args.output_patient_id = "UNKNOWN"

    variants = variant_collection_from_args(args)
    logger.info(variants)

    mhc_alleles = mhc_alleles_from_args(args)
    logger.info("MHC alleles: %s", mhc_alleles)
    mhc_predictor = mhc_binding_predictor_from_args(args)

    # generator that for each variant gathers all RNA reads, both those
    # supporting the variant and reference alleles
    reads_generator = allele_reads_generator_from_args(args)

    ranked_list = ranked_vaccine_peptides(
        reads_generator=reads_generator,
        mhc_predictor=mhc_predictor,
        vaccine_peptide_length=args.vaccine_peptide_length,
        padding_around_mutation=args.padding_around_mutation,
        max_vaccine_peptides_per_variant=args.max_vaccine_peptides_per_mutation,
        min_reads_supporting_cdna_sequence=args.min_reads_supporting_variant_sequence,
        min_epitope_score=args.min_epitope_score)

    ranked_list_for_report = ranked_list[:args.max_mutations_in_report]
    df = dataframe_from_ranked_list(ranked_list_for_report)
    logger.debug(df)

    if args.output_csv:
        df.to_csv(args.output_csv, index=False)

    template_data = compute_template_data(
        ranked_variants_with_vaccine_peptides=ranked_list_for_report,
        mhc_alleles=mhc_alleles,
        variants=variants,
        bam_path=args.bam)

    template_data.update({
        'args': interesting_args,
        'patient_id': args.output_patient_id,
        'final_review': args.output_final_review,
        'reviewers': args.output_reviewed_by.split(','),
    })

    # save pickled template data if necessary. this is meant to make a dev's life easier:
    # as of time of writing, vaxrank takes ~25 min to run, most of which is core logic
    # that creates the template_data dictionary. the formatting is super fast, and it can
    # be useful to save template_data to be able to iterate just on the formatting.
    if args.output_pickle_file:
        with open(args.output_pickle_file, 'wb') as f:
            pickle.dump(template_data, f)
        logger.info('Wrote pickled template data to %s', args.output_pickle_file)

    return template_data


def main(args_list=None):
    """
    Script to generate vaccine peptide predictions from somatic cancer variants,
    patient HLA type, and tumor RNA-seq data.

    Example usage:
        vaxrank
            --vcf somatic.vcf \
            --bam rnaseq.bam \
            --vaccine-peptide-length 25 \
            --output-csv vaccine-peptides.csv
    """
    if args_list is None:
        args_list = sys.argv[1:]

    args = arg_parser.parse_args(args_list)
    logger.info(vars(args))

    if (len(args.output_csv) == 0 and
            len(args.output_ascii_report) == 0 and
            len(args.output_html_report) == 0 and
            len(args.output_pdf_report) == 0):
        raise ValueError(
            "Must specify at least one of: --output-csv, "
            "--output-ascii-report, "
            "--output-html-report, "
            "--output-pdf-report")

    template_data = load_template_data(args)

    if args.output_ascii_report:
        make_ascii_report(
            template_data=template_data,
            ascii_report_path=args.output_ascii_report)

    if args.output_html_report:
        make_html_report(
            template_data=template_data,
            html_report_path=args.output_html_report)

    if args.output_pdf_report:
        make_pdf_report(
            template_data=template_data,
            pdf_report_path=args.output_pdf_report)
