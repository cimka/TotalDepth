"""
RP66V1 HTML support.

"""
import logging
import os
import typing

from TotalDepth.RP66V1.core import LogicalFile, File, StorageUnitLabel, LogPass, RepCode, AbsentValue
from TotalDepth.RP66V1.core.LogicalRecord import EFLR, IFLR
from TotalDepth.RP66V1.core.stringify import stringify_object_by_type
from TotalDepth.common import Slice
from TotalDepth.util import XmlWrite


logger = logging.getLogger(__file__)

# Examples: https://jrgraphix.net/r/Unicode/25A0-25FF
UNICODE_SYMBOLS = {
    'TAPE_DRIVE': '\u2707',
    'TABLE': '\u2637',
    'TABLE_FINE': '\u2586',
    'CHANNEL': '\u2307',
}

CSS_RP66V1 = """/* CSS for RP66V1 */
body {
font-size:      12px;
font-family:    arial,helvetica,sans-serif;
margin:         6px;
padding:        6px;
}
h1 {
color:            darkgoldenrod;
font-family:      sans-serif;
font-size:        14pt;
font-weight:      bold;
}
h2 {
color:          IndianRed;
font-family:    sans-serif;
font-size:      14pt;
font-weight:    normal;
}
h3 {
color:          Black;
font-family:    sans-serif;
font-size:      12pt;
font-weight:    bold;
}
h4 {
color:          FireBrick;
font-family:    sans-serif;
font-size:      10pt;
font-weight:    bold;
}
span.line {
color:           slategrey;
/*font-style:    italic; */
}
span.file {
 color:         black;
 font-style:    italic;
}

table.filetable {
    border:         2px solid black;
    font-family:    monospace;
    color:          black;
}
th.filetable, td.filetable {
    /* border: 1px solid black; */
    border: 1px;
    border-top-style:solid;
    border-right-style:dotted;
    border-bottom-style:none;
    border-left-style:none;
    vertical-align:top;
    padding: 2px 6px 2px 6px; 
}


table.eflr {
    border:         2px solid black;
    border-collapse:   collapse;
    font-family:    monospace;
    color:          black;
}
th.eflr, td.eflr {
    border: 1px solid black;
    /* border-top-style:solid; */
    /* border-right-style:dotted; */
    /* border-bottom-style:none; */
    /* border-left-style:none; */
    vertical-align:top;
    padding: 2px 6px 2px 6px; 
}

table.sul {
    border:            2px solid black;
    border-collapse:   collapse;
    /* font-family:       monospace; */
    color:             black;
}

th.sul, td.sul {
    border:            1px solid black;
    vertical-align:    top;
    padding:           2px 6px 2px 6px; 
}

table.monospace {
    border:            2px solid black;
    border-collapse:   collapse;
    font-family:       monospace;
    color:             black;
}
th.monospace, td.monospace {
    border:            1px solid black;
    vertical-align:    top;
    padding:           2px 6px 2px 6px; 
}
"""


def html_write_storage_unit_label(sul: StorageUnitLabel.StorageUnitLabel, xhtml_stream: XmlWrite.XhtmlStream) -> None:
    with XmlWrite.Element(xhtml_stream, 'h2'):
        xhtml_stream.characters('Storage Unit Label')
    table = [
        ['KEY', 'VALUE'],
        ['Storage Unit Sequence Number:', f'{sul.storage_unit_sequence_number:d}'],
        ['DLIS Version:', f'{sul.dlis_version.decode("ascii")}'],
        ['Storage Unit Structure:', f'{sul.storage_unit_structure.decode("ascii")}'],
        ['Maximum Record Length:', f'{sul.maximum_record_length:d}'],
        ['Storage Set Identifier:', f'{sul.storage_set_identifier.decode("ascii")}'],
    ]
    html_write_table(table, xhtml_stream, class_style='sul')


def html_write_table(table_as_strings: typing.List[typing.List[str]],
                     xhtml_stream: XmlWrite.XhtmlStream,
                     class_style) -> None:
    if len(table_as_strings):
        with XmlWrite.Element(xhtml_stream, 'table', {'class': class_style}):
            with XmlWrite.Element(xhtml_stream, 'tr', {}):
                for cell in table_as_strings[0]:
                    with XmlWrite.Element(xhtml_stream, 'th', {'class': class_style}):
                        xhtml_stream.characters(cell)
            for row in table_as_strings[1:]:
                with XmlWrite.Element(xhtml_stream, 'tr', {}):
                    for cell in row:
                        with XmlWrite.Element(xhtml_stream, 'td', {'class': class_style}):
                            assert isinstance(cell, str), f'{cell} is not a string but {type(cell)}'
                            xhtml_stream.charactersWithBr(cell)


def html_write_EFLR_as_table(eflr: EFLR.ExplicitlyFormattedLogicalRecord, xhtml_stream: XmlWrite.XhtmlStream) -> None:
        if eflr.is_key_value():
            table_as_strings = eflr.key_values(stringify_function=stringify_object_by_type, sort=True)
        else:
            table_as_strings = eflr.table_as_strings(stringify_function=stringify_object_by_type, sort=True)
        html_write_table(table_as_strings, xhtml_stream, class_style='eflr')


class HTMLFrameArraySummary(typing.NamedTuple):
    ident: bytes
    num_frames: int
    channels: typing.Tuple[bytes]
    x_start: float
    x_stop: float
    x_units: bytes
    href: str


def _write_log_pass_content_in_html(
        rp66_file: File.FileRead,
        visible_record_positions: LogicalFile.VisibleRecordPositions,
        logical_file: LogicalFile.LogicalFile,
        xhtml_stream: XmlWrite.XhtmlStream,
        # Used for anchor
        logical_file_index: int,
        number_of_preceeding_eflrs: int,
        *,
        frame_slice: Slice.Slice) -> typing.Tuple[HTMLFrameArraySummary]:
    assert logical_file.has_log_pass
    ret = []
    lp: LogPass.LogPass = logical_file.log_pass
    frame_array: LogPass.FrameArray
    for fa, frame_array in enumerate(lp.frame_arrays):
        anchor = _anchor(logical_file_index, number_of_preceeding_eflrs, fa)
        with XmlWrite.Element(xhtml_stream, 'a', {'id': anchor}):
            pass
        with XmlWrite.Element(xhtml_stream, 'h3'):
            xhtml_stream.characters(f'Frame Array: {stringify_object_by_type(frame_array.ident)} [{fa}/{len(lp.frame_arrays)}]')
        ret.append(
            _write_frame_array_in_html(
                rp66_file,
                visible_record_positions,
                logical_file,
                frame_array,
                frame_slice,
                anchor,
                xhtml_stream,
            )
       )
    return tuple(ret)


def _write_frame_array_in_html(
        rp66_file: File.FileRead,
        visible_record_positions: LogicalFile.VisibleRecordPositions,
        logical_file: LogicalFile.LogicalFile,
        frame_array: LogPass.FrameArray,
        frame_slice: Slice.Slice,
        anchor: str,
        xhtml_stream: XmlWrite.XhtmlStream,
    ) -> HTMLFrameArraySummary:

    with XmlWrite.Element(xhtml_stream, 'h4'):
        xhtml_stream.characters('Frame Data')
    iflrs: typing.List[LogicalFile.IFLRData] = logical_file.iflr_position_map[frame_array.ident]
    if len(iflrs):
        # NOTE: +1
        num_frames = frame_slice.count(len(iflrs)) + 1
        logger.info(f'Creating space for {num_frames} frames from {len(iflrs)} IFLRs.')
        frame_array.init_arrays(num_frames)
        interval = (iflrs[-1].x_axis - iflrs[0].x_axis) / (len(iflrs) - 1) if len(iflrs) > 1 else 0.0
        with XmlWrite.Element(xhtml_stream, 'p'):
            xhtml_stream.characters(
                f'Available frames: {len(iflrs)}'
                f' X axis from {float(iflrs[0].x_axis):0.3f}'
                f' to {float(iflrs[-1].x_axis):0.3f}'
                f' interval {interval:0.3f}'
                f' [{stringify_object_by_type(frame_array.x_axis.units)}]'
            )
        with XmlWrite.Element(xhtml_stream, 'p'):
            xhtml_stream.characters(
                f'Frames {frame_slice.long_str(len(iflrs))} frame(s).'
                f' Number of frames created: {num_frames}'
                f' Numpy total memory: {frame_array.sizeof_array:,d} bytes'
            )
        # TODO: Make this code part of the FrameArray, or a free function in LogPass returning num_frames.
        # See also the code in Scan.py which is very similar
        for f, frame_number in enumerate(frame_slice.range(len(iflrs))):
            # logger.info(f'Reading frame {frame_number} into frame {f}.')
            iflr_frame_number, lrsh_position, x_axis = iflrs[frame_number]
            vr_position = visible_record_positions.visible_record_prior(lrsh_position)
            fld: File.FileLogicalData = rp66_file.get_file_logical_data(vr_position, lrsh_position)
            iflr = IFLR.IndirectlyFormattedLogicalRecord(fld.lr_type, fld.logical_data)
            frame_array.read(iflr.logical_data, f)
        frame_table = [
            ['Channel', 'O', 'C', 'Rep Code', 'Dims', 'Count', 'Units', 'Long Name',
             'Size', 'Absent', 'Min', 'Mean', 'Std.Dev.', 'Max', 'dtype'],
        ]
        for channel in frame_array.channels:
            # arr = channel.array
            arr = AbsentValue.mask_absent_values(channel.array)
            frame_table.append(
                [
                    channel.ident.I.decode("ascii"),
                    f'{channel.ident.O}',
                    f'{channel.ident.C}',
                    f'{channel.rep_code:d} ({RepCode.REP_CODE_INT_TO_STR[channel.rep_code]})',
                    stringify_object_by_type(channel.dimensions),
                    stringify_object_by_type(channel.count),
                    stringify_object_by_type(channel.units),
                    stringify_object_by_type(channel.long_name),
                    f'{arr.size:d}',
                    # NOTE: Not the masked array!
                    f'{AbsentValue.count_of_absent_values(channel.array):d}',
                    f'{arr.min():.3f}',
                    f'{arr.mean():.3f}',
                    f'{arr.std():.3f}',
                    f'{arr.max():.3f}',
                    f'{arr.dtype}',
                ]
            )
        html_write_table(frame_table, xhtml_stream, class_style='monospace')
        x_axis_start = iflrs[0].x_axis
        x_axis_stop = iflrs[-1].x_axis
    else:
        with XmlWrite.Element(xhtml_stream, 'p'):
            xhtml_stream.characters('No frames.')
        x_axis_start = x_axis_stop = 0.0
    return HTMLFrameArraySummary(
                frame_array.ident.I,
                len(iflrs),
                tuple(c.ident.I for c in frame_array.channels),
                x_axis_start,
                x_axis_stop,
                frame_array.x_axis.units,
                anchor,
            )


# def _anchor(*args: typing.Tuple[int, ...]) -> str:
def _anchor(*args) -> str:
    arg_list = '_'.join(f'{arg:d}' for arg in args)
    return f'anchor_{arg_list}'


def html_write_table_of_contents(
        logical_file_sequence: LogicalFile.LogicalIndex,
        xhtml_stream: XmlWrite.XhtmlStream) -> None:
    """Write out the table of contents."""
    with XmlWrite.Element(xhtml_stream, 'h2'):
        xhtml_stream.characters('Table of Contents')
    with XmlWrite.Element(xhtml_stream, 'ol'):
        logical_file: LogicalFile.LogicalFile
        for index_lf, logical_file in enumerate(logical_file_sequence.logical_files):
            with XmlWrite.Element(xhtml_stream, 'li'):
                xhtml_stream.characters(f'Logical File [{index_lf}/{len(logical_file_sequence.logical_files)}]')
            with XmlWrite.Element(xhtml_stream, 'ol'):
                lrsh_position: File.LogicalRecordPosition
                eflr: EFLR.ExplicitlyFormattedLogicalRecord
                for index_eflr, (lrsh_position, eflr) in enumerate(logical_file.eflrs):
                    with XmlWrite.Element(xhtml_stream, 'li'):
                        with XmlWrite.Element(xhtml_stream, 'a', {'href': f'#{_anchor(index_lf, index_eflr)}'}):
                            xhtml_stream.characters(f'{eflr.set.type.decode("ascii")}')
                        xhtml_stream.literal('&nbsp;')
                        xhtml_stream.characters(f'Shape: {eflr.shape}')
                if logical_file.has_log_pass:
                    with XmlWrite.Element(xhtml_stream, 'li'):
                        with XmlWrite.Element(xhtml_stream, 'a', {'href': f'#{_anchor(index_lf, len(logical_file.eflrs))}'}):
                            xhtml_stream.characters(f'Log Pass with {len(logical_file.log_pass)} Frame Arrays')
                        with XmlWrite.Element(xhtml_stream, 'ol'):
                            for index_fa, frame_array in enumerate(logical_file.log_pass):
                                with XmlWrite.Element(xhtml_stream, 'li'):
                                    attrs = {'href': f'#{_anchor(index_lf, len(logical_file.eflrs), index_fa)}'}
                                    # xhtml_stream.characters(f'Frame Array:')
                                    with XmlWrite.Element(xhtml_stream, 'a', attrs):
                                        xhtml_stream.characters(f'{stringify_object_by_type(frame_array.ident)}')
                                    xhtml_stream.characters(f' with {len(frame_array.channels)} channels')
                                    xhtml_stream.characters(f' and {len(logical_file.iflr_position_map[frame_array.ident])} frames')


def html_write_file_info(path_in: str, xhtml_stream: XmlWrite.XhtmlStream) -> None:
    with XmlWrite.Element(xhtml_stream, 'h2'):
        xhtml_stream.characters('File information')
    table = [
        ['KEY', 'VALUE'],
        ['File Path:', path_in],
        ['File size:', f'{os.path.getsize(path_in):,d}'],
    ]
    html_write_table(table, xhtml_stream, class_style='monospace')


class HTMLLogicalFileSummary(typing.NamedTuple):
    eflr_types: typing.Tuple[bytes]
    frame_arrays: typing.Tuple[HTMLFrameArraySummary]


class HTMLBodySummary(typing.NamedTuple):
    link_text: str
    logical_files: typing.Tuple[HTMLLogicalFileSummary]


def html_write_body(
        rp66_file: File.FileRead,
        logical_file_sequence: LogicalFile.LogicalIndex,
        frame_slice: Slice.Slice,
        xhtml_stream: XmlWrite.XhtmlStream,
    ) -> HTMLBodySummary:
    """Write out the <body> of the document."""
    with XmlWrite.Element(xhtml_stream, 'h1'):
        xhtml_stream.characters('RP66V1 File Data Summary')
    html_write_file_info(logical_file_sequence.path, xhtml_stream)
    html_write_storage_unit_label(logical_file_sequence.storage_unit_label, xhtml_stream)
    html_write_table_of_contents(logical_file_sequence, xhtml_stream)
    logical_file_summaries: typing.List[HTMLLogicalFileSummary] = []
    logical_file: LogicalFile.LogicalFile
    for lf, logical_file in enumerate(logical_file_sequence.logical_files):
        eflr_types: typing.List[bytes] = []
        with XmlWrite.Element(xhtml_stream, 'h2'):
            xhtml_stream.characters(f'Logical File [{lf}/{len(logical_file_sequence.logical_files)}]')
        eflr_position: LogicalFile.PositionEFLR
        for e, eflr_position in enumerate(logical_file.eflrs):
            eflr_types.append(eflr_position.eflr.set.type)
            header = [
                f'EFLR: {eflr_position.eflr.set.type.decode("ascii")}',
                f'Shape: {eflr_position.eflr.shape}'
            ]
            with XmlWrite.Element(xhtml_stream, 'a', {'id': f'{_anchor(lf, e)}'}):
                pass
            with XmlWrite.Element(xhtml_stream, 'h3'):
                xhtml_stream.characters(' '.join(header))
            with XmlWrite.Element(xhtml_stream, 'p'):
                xhtml_stream.characters(f'Location: {eflr_position.lrsh_position}')
            if eflr_position.eflr.set.type == b'FILE-HEADER':
                obj = eflr_position.eflr.objects[0]
                with XmlWrite.Element(xhtml_stream, 'p'):
                    xhtml_stream.characters(
                        f'File-Header Object "{obj.name.I.decode("ascii")}" O: {obj.name.O} C: {obj.name.C}:'
                    )
            elif eflr_position.eflr.set.type == b'ORIGIN':
                # Slightly special case with ORIGIN records as they contain the Defining Origin of the Logical File.
                # [RP66V1 Section 5.2.1 Origin Objects]
                obj = eflr_position.eflr.objects[0]
                with XmlWrite.Element(xhtml_stream, 'p'):
                    xhtml_stream.characters(
                        f'Logical File Defining Origin "{obj.name.I.decode("ascii")}" O: {obj.name.O} C: {obj.name.C}:'
                    )
            html_write_EFLR_as_table(eflr_position.eflr, xhtml_stream)
        with XmlWrite.Element(xhtml_stream, 'h3'):
            xhtml_stream.characters('Log Pass')
        if logical_file.has_log_pass:
            with XmlWrite.Element(xhtml_stream, 'a', {'id': f'{_anchor(lf, len(logical_file.eflrs))}'}):
                pass
            frame_array_summary = _write_log_pass_content_in_html(
                    rp66_file, logical_file_sequence.visible_record_positions,
                    logical_file, xhtml_stream,
                    lf, len(logical_file.eflrs),
                    frame_slice=frame_slice,
            )
            logical_file_summaries.append((HTMLLogicalFileSummary(tuple(eflr_types), frame_array_summary)))
        else:
            with XmlWrite.Element(xhtml_stream, 'p'):
                xhtml_stream.characters('NO Log Pass for this Logical Record')
            logical_file_summaries.append((HTMLLogicalFileSummary(tuple(eflr_types), tuple())))
    return HTMLBodySummary(
        logical_file_sequence.storage_unit_label.storage_set_identifier.decode('ascii'),
        tuple(logical_file_summaries),
    )


def html_scan_RP66V1_file_data_content(path_in: str, fout: typing.TextIO,
                                       *, frame_slice: Slice.Slice) -> HTMLBodySummary:
    """
    Scans all of every EFLR and IFLR in the file and writes to HTML.
    Similar to TotalDepth.RP66V1.core.Scan.scan_RP66V1_file_data_content
    Return text to use as a link.
    """
    with open(path_in, 'rb') as fobj:
        # FIXME: Make this a bit of a generic pattern like LIS by:
        # - Get LogicalIndex take a File.FileRead
        # - Rename LogicalIndex to be FileIndex or similar.
        rp66v1_file = File.FileRead(fobj)
        logical_file_sequence = LogicalFile.LogicalIndex(rp66v1_file, path_in)

        with XmlWrite.XhtmlStream(fout) as xhtml_stream:
            with XmlWrite.Element(xhtml_stream, 'head'):
                with XmlWrite.Element(xhtml_stream, 'meta', {
                    'charset': "UTF-8",
                    'name': "viewport",
                    'content': "width=device-width, initial-scale=1",
                }):
                    pass
                with XmlWrite.Element(xhtml_stream, 'title'):
                    xhtml_stream.charactersWithBr(f'RP66V1 Scan of {path_in}')
                with XmlWrite.Element(xhtml_stream, 'style'):
                    xhtml_stream.literal(CSS_RP66V1)
            with XmlWrite.Element(xhtml_stream, 'body'):
                return html_write_body(rp66v1_file, logical_file_sequence, frame_slice, xhtml_stream)
        # return logical_file_sequence.storage_unit_label.storage_set_identifier.decode('ascii')
