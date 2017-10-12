from os.path import exists
from os import remove as rm
from rf import RFStream, rfstats
from collections import defaultdict
import time


code_start_time = time.time()


def asdf_rf_calc(in_ds, ASDF_temp, event_id=None):
    """

    :param in_ds: filename in
    :param ASDF_temp: temporary filename out
    :param event_id: event resource id for desired quake, defalut is None so all events will be processed
    :return: None
    """


    # remove the outputs if they exist
    if exists(ASDF_temp):
        rm(ASDF_temp)



    #get event catalogue
    event_cat = in_ds.events


    def process_RF(st, inv):

        all_stn_RF = RFStream()

        # make dictionary of lists containing indexes of the traces with the same referred event
        event_dict = defaultdict(list)
        for _i, tr in enumerate(st):
            event_dict[tr.stats.asdf.event_ids[0]].append(_i)

        for event_res_id in event_dict.keys():
            if not event_res_id == event_id and event_id != None:
                continue

            print(event_res_id)

            if not len(event_dict[event_res_id]) == 3:
                print 'Not enough components'
                continue

            # Make sure the referred event matches for each stream
            ref_events = []
            ref_events.append(st[event_dict[event_res_id][0]].stats.asdf.event_ids[0])
            ref_events.append(st[event_dict[event_res_id][1]].stats.asdf.event_ids[0])
            ref_events.append(st[event_dict[event_res_id][2]].stats.asdf.event_ids[0])

            if not all(x == ref_events[0] for x in ref_events):
                print "Events are not the same"
                continue

            rf_stream = RFStream(traces=[st[event_dict[event_res_id][0]], st[event_dict[event_res_id][1]],
                                         st[event_dict[event_res_id][2]]])

            print(rf_stream)


            stats = None
            found_event = False
            for event in event_cat:
                if event.resource_id == ref_events[0]:
                    found_event = True
                    stats = rfstats(station=inv[0][0], event=event, phase='P', dist_range=(10, 90))
                    print(stats)
                    temp_event_res_id = str(event.resource_id)
            if not found_event:
                print 'Event not in Catalogue'

            # Stats might be none if epicentral distance of earthquake is outside dist_range
            if not stats == None:



                for tr in rf_stream:
                    tr.stats.update(stats)
                    tr.stats.asdf.labels = [temp_event_res_id]
                    print(tr.stats)

                rf_stream.filter('bandpass', freqmin=0.05, freqmax=1.)
                rf_stream.rf(method='P', trim=(-10, 30), downsample=50, deconvolve='time')




                all_stn_RF.extend(rf_stream)


        print(all_stn_RF)
        return all_stn_RF




    in_ds.process(process_function=process_RF, output_filename=ASDF_temp, tag_map={"earthquake": "receiver_function"})

    print '\n'