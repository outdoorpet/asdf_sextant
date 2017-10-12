import pyasdf
from os.path import join, exists
from os import remove as rm
import numpy as np
from rf import rfstats

def clean_rf_ds(in_ds, ASDF_temp, ASDF_out):
    """

    :param in_ds: input asdf dataset
    :param ASDF_temp: temporary filename out
    :param ASDF_out: output ASDF filename
    :return: None
    """

    # remove the outputs if they exist
    if exists(ASDF_out):
        rm(ASDF_out)

    # now reopen the input and output RF asdf file and add in waveforms
    tmp_ds = pyasdf.ASDFDataSet(ASDF_temp)
    out_ds = pyasdf.ASDFDataSet(ASDF_out)


    #open event catloag
    cat = in_ds.events
    
    out_ds.add_quakeml(cat)

    added_events = []
    
    
    # go through all stations in tmp_ds
    for sta in tmp_ds.waveforms.list():
        rf_sta_accessor = tmp_ds.waveforms[sta]
    
        if "receiver_function" in rf_sta_accessor.get_waveform_tags():
    
            # go through each receiver function trace and find the corresponding earthquake trace and appropriate metadata
            for asdf_id in rf_sta_accessor.list():
                if asdf_id == "StationXML":
                    continue

                rf_st = rf_sta_accessor[asdf_id]
                rf_tr = rf_st[0]
                # print(rf_st)
                event_res_id = rf_tr.stats.asdf.labels[0]
    
                # get the event object with the assocatieted resource id
                for event in cat:
                    if not event.resource_id.id == event_res_id:
                        continue

    
    
                origin_info = event.preferred_origin() or event.origins[0]
    
                for eq_sta_accessor in in_ds.ifilter(in_ds.q.event == event_res_id):
                    chan_eq_inv = eq_sta_accessor.StationXML
                    # print(chan_eq_inv)
    
    
                    for eq_asdf_id in eq_sta_accessor.list():
                        if eq_asdf_id == "StationXML":
                            continue
    
                        split_id = eq_asdf_id.split("__")
                        eq_tr_id = split_id[0]
                        # print(eq_tr_id)
    
                        eq_st = eq_sta_accessor[eq_asdf_id]
                        eq_tr = eq_st[0]
    
                        # print(eq_st)
    
                        #get the earthquake auxillary info for trace
                        arrival_aux = in_ds.auxiliary_data.ArrivalData[event_res_id.split("=")[1]][eq_tr.get_id().replace(".", "_")]
    
    
                        # get the related inventory for the channel
                        select_inv = chan_eq_inv.select(network=eq_tr_id.split(".")[0],
                                                        station=eq_tr_id.split(".")[1],
                                                        location=eq_tr_id.split(".")[2],
                                                        channel=eq_tr_id.split(".")[3])
    
                        # print(select_inv)
    
                        # modify the start and end times for each channel and station
                        select_inv[0][0].start_date = eq_tr.stats.starttime
                        select_inv[0][0].end_date = eq_tr.stats.endtime
                        select_inv[0][0].creation_date = eq_tr.stats.starttime
                        select_inv[0][0].termination_date = eq_tr.stats.endtime
                        select_inv[0][0][0].start_date = eq_tr.stats.starttime
                        select_inv[0][0][0].end_date = eq_tr.stats.endtime
    
                        # transfer parametric data on eq arrival into auxillary data
                        # as well as calculate some new data required for the RFs
                        data_type = "ArrivalData"
                        data_path = event_res_id.split("=")[1] + "/" + eq_tr.get_id().replace('.', '_')
    
    
                        #write the earthquake waveforms if they ahvenyt been already
                        if not event_res_id in added_events:
                            # write the trace and inventory to out asdf
                            out_ds.add_waveforms(eq_tr, tag="earthquake", event_id=event_res_id)
                            out_ds.add_stationxml(select_inv)
                            out_ds.add_auxiliary_data(data=np.array([0]),
                                                            data_type=data_type,
                                                            path=data_path,
                                                            parameters=arrival_aux.parameters)
    
                added_events.append(event_res_id)
    
                # make a copy of the inventory
                rf_inv = select_inv
    
    
    
    
                # modify the channel code
                # print(rf_tr.stats)
    
                rf_inv[0][0].code = rf_tr.stats.station
                rf_inv[0][0][0].code = rf_tr.stats.channel
                rf_inv[0][0][0].location_code = rf_tr.stats.location
                rf_inv[0][0][0].sample_rate = rf_tr.stats.sampling_rate
    
                # print(rf_inv[0][0])
    
                # calculate extra parameters for reciever functions
                stats = rfstats(station=rf_inv[0][0], event=event, phase='P', dist_range=(10, 90))
                # print(stats)
    
                parameters = {"P": arrival_aux.parameters["P"],
                              "P_as": arrival_aux.parameters["P_as"],
                              "distkm": arrival_aux.parameters["distkm"],
                              "dist_deg": arrival_aux.parameters["dist_deg"],
                              "back_azimuth": stats["back_azimuth"],
                              "slowness": stats["slowness"],
                              "inclination": stats["inclination"]}
    
                # print(rf_inv)
                # print(rf_inv[0][0])
                # print(rf_inv[0][0][0])
    
                data_path = event_res_id.split("=")[1] + "/" + rf_tr.get_id().replace('.', '_')
                # print(data_path)
    
                # write the trace and inventory to out asdf
                out_ds.add_waveforms(rf_tr, tag="receiver_function", event_id=event_res_id)
                out_ds.add_stationxml(rf_inv)
                out_ds.add_auxiliary_data(data=np.array([0]),
                                          data_type=data_type,
                                          path=data_path,
                                          parameters=parameters)
    
                # print(eq_tr)


    del tmp_ds
    del out_ds
    
